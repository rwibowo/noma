from __future__ import absolute_import, unicode_literals
from celery import shared_task, current_task
import os, pathlib
from .models import NomaGrp, NomaSet, queGrp, queSet, NomaStrMap
from .utils import nomaMain, nomaRetrace
from django.db import connection, connections
import pandas as pd
import noma.tfunc

@shared_task
def nomaExec(id, total_count):
    grp = NomaGrp.objects.get(pk=int(id))
    sdir = pathlib.Path(grp.sdir)
    ldir = pathlib.Path(grp.ldir)
    f=open(ldir / (grp.name + '.log'), "w+")
    f.write("Executing NOMA Group: %s from Source DIR: %s\n\n" % (grp, sdir))
    dfsm = pd.DataFrame.from_records(NomaStrMap.objects.all().values())
    sm = dfsm.groupby('ctag')
    smap = {}
    for name,group in sm:
        smap[name] = {}
        for row,data in group.iterrows(): smap[name].update({data['ostr'] : data['cstr']})
    i = 0
    for grpset in grp.sets.all():
        set = NomaSet.objects.get(name=grpset.set)
        f.write("Executing NOMA Set: %s\n" % set.name)
        f.write("   Set Type: %s\n" % set.type)
        f.write("   Target Table: %s\n" % grpset.ttbl)
        f.write("   NOMA Actions Sequences:\n")
        hdrs = [] if grp.gtag == None else ['gtag']
        sfiles = []
        acts = set.acts.all()
        for act in acts:
            if act.fname != None and act.fname not in hdrs: hdrs.append(act.fname)
            f.write("       %s  -  %s  -  %s   -  %s\n" % (act.seq, act.fname, act.sepr, act.eepr))
        f.write("\n")
        sfile = sdir / grpset.sfile
        tfile = ldir / (set.name + '.tsv')
        f.write("   Source Files: %s\n" % sfile)
        if set.type == 'p2':
            df = pd.read_sql_query(grpset.sfile, connections['xnaxdr'])
            dfgf = df.groupby('pfile')
            for name in dfgf: sfiles.append(name.strip())
        else:
            sfiles = list(sdir.glob(grpset.sfile))
        if sfiles:
            for sfile in sfiles:
                dfsf = dfgf.get_group(sfile) if set.type == 'p2' else ''
                f.write("       %s\n" % sfile.name)
                i = i + 1
                f.write(nomaMain(sfile, tfile, set, acts, smap, dfsf, grp.gtag))
                f.flush()
                os.fsync(f.fileno())
                if grpset.ttbl != None:
                    hdr = ','.join(hd for hd in hdrs)
                    tf = "'%s'" % ('/'.join(fn for fn in str(tfile).split('\\')))
                    sqlq = "LOAD DATA LOCAL INFILE %s INTO TABLE %s FIELDS TERMINATED BY '\t' (%s)" % (tf, grpset.ttbl, hdr)
                    with connections['xnaxdr'].cursor() as cursor:
                        cursor.execute(sqlq)
                    f.write("Succesfully Loaded:\n %s\n" % sqlq)
                current_task.update_state(state='PROGRESS',
                                          meta={'current': i, 'total': total_count,
                                                'percent': int((float(i) / total_count) * 100)})
        else:
            f.write("       No file matching %s\n" % grpset.source_file)     
    f.close()
    return {'current': total_count, 'total': total_count, 'percent': 100}

@shared_task
def nomaQExe(id, total_count):
    grp = queGrp.objects.get(pk=int(id))
    ldir = pathlib.Path(grp.ldir)
    tfile = ldir / grp.tfile
    writer = pd.ExcelWriter(tfile, engine='xlsxwriter')
    workbook = writer.book
    format_col = workbook.add_format({'align':'left','font_size':9})
    format_hdr = workbook.add_format({'bold': True, 'fg_color': '#D7E4BC'})
    format_title = workbook.add_format({'bold': True, 'fg_color': '#00FFFF', 'font_size':14})
    format_add = workbook.add_format({'bold': True, 'bg_color': '#FFFF00'})
    format_del = workbook.add_format({'bold': True, 'bg_color': '#FF0000'})
    format_mod = workbook.add_format({'bold': True, 'bg_color': '#FF00FF'})
    Indexsheet = workbook.add_worksheet('Index')
    Indexsheet.write(0,0, grp.name, format_title)
    Indexsheet.write(0,1, '', format_title)
    Indexsheet.set_column(0, 0, 3)
    Indexsheet.set_column(1, 1, 80)
    f=open(ldir / (grp.name + '.log'), "w+")
    f.write("Executing Que Group: %s Output to DIR: %s\n\n" % (grp, ldir))
    i, j = 0, 0
    for grpset in grp.sets.all():
        j += 1
        set = queSet.objects.get(name=grpset.set)
        f.write("Executing Query Set: %s\n" % set.name)
        Indexsheet.write(j+1,0, set.name, format_hdr)
        Indexsheet.write(j+1,1, '', format_hdr)
        f.write("   NOMA Queries Sequences:\n")
        for sq in set.Sqls.all():
            i += 1
            j += 1
            ps = "'%s',%s,%s,%s" % (sq.stbl,sq.qpar,grpset.spar,grp.gpar)
            pars = '(%s)' % ','.join(p for p in ps.split(',') if p != '')
            ps = pars if len(pars) < 100 else pars[:120]
            f.write("       %s  -  %s  -   %s%s\n" % (sq.seq, sq.name, sq.qfunc, pars))
            hl = 'internal:%s!A1' % sq.name
            Indexsheet.write_url(j+1,1, hl, string='%s: %s' % (sq.name,ps))
            qfun = 'noma.tfunc.%s%s' % (sq.qfunc, pars)
            sqlq = eval(qfun) 
            df = pd.read_sql_query(sqlq, connections['xnaxdr'])
            df.to_excel(writer, sheet_name=sq.name, index=False, startrow=1, header=False)
            worksheet = writer.sheets[sq.name]
            worksheet.conditional_format(1,0,len(df.index),len(df.columns)-1,{'type':'text','criteria':'containing','value':':##','format':format_add})
            worksheet.conditional_format(1,0,len(df.index),len(df.columns)-1,{'type':'text','criteria':'containing','value':'##:','format':format_del})
            worksheet.conditional_format(1,0,len(df.index),len(df.columns)-1,{'type':'text','criteria':'containing','value':'<>','format':format_mod})
            worksheet.write_url(0,0, 'internal:Index!A1')
            for cnum, value in enumerate(df.columns.values):
                worksheet.write(0, cnum, value, format_hdr)
            cn = len(df.columns)-1
            worksheet.autofilter(0,0,0,cn)
            worksheet.set_column(0, cn, None, format_col)
            worksheet.freeze_panes(1, 0)
            for c, col in enumerate(df.columns):
                clen = max(df[col].astype(str).map(len).max(), len(str(df[col].name))+1) + 1
                worksheet.set_column(c, c, clen)
            f.write("       Succesfully Exported:\n\n")
            if 'pidx' in df.columns.values: nomaRetrace(df, '%s%s' % (ldir, sq.name))
            current_task.update_state(state='PROGRESS',
                                      meta={'current': i, 'total': total_count,
                                            'percent': int((float(i) / total_count) * 100)})
        f.write("\n")
        
    writer.save()
    f.close()
    return {'current': total_count, 'total': total_count, 'percent': 100}


            