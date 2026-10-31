"""Deterministic evidence summaries, with no LLM judging or authorship claims."""
import difflib


def claim_evidence(state, results):
    portfolio=state.get('portfolio',{})
    rows=[]
    for topic in portfolio.get('dsa_topics',[]):
        indexes=[i for i,t in enumerate(state['tasks']) if t['topic']==topic]
        passed=sum(results[i].get('question_results',{}).get('dsa',{}).get('passed',0) for i in indexes)
        total=sum(results[i].get('question_results',{}).get('dsa',{}).get('total',0) for i in indexes)
        attempted=any(results[i].get('attempted',{}).get('dsa') for i in indexes)
        rows.append({'claim':topic.replace('_',' '),'tested':'Coding questions: '+', '.join(state['tasks'][i]['tier'] for i in indexes),
            'result':'Not tested' if not total else 'Not attempted' if not attempted else 'Demonstrated' if passed==total else 'Needs practice',
            'passed':passed,'total':total,'scope':'Only the problems in this assessment.'})
    # A Python function exercise does not establish framework, security, or whole-project expertise.
    for skill in dict.fromkeys(portfolio.get('skills',[])):
        if skill.strip().lower() in {'python','python3','python 3'}:
            passed=sum(r.get('question_results',{}).get('project',{}).get('passed',0) for r in results)
            total=sum(r.get('question_results',{}).get('project',{}).get('total',0) for r in results)
            rows.append({'claim':skill,'tested':'Python data-processing tasks','result':'Demonstrated' if total and passed==total else 'Needs practice' if any(r.get('attempted',{}).get('project') for r in results) else 'Not attempted','passed':passed,'total':total,'scope':'Does not verify the full project or its authorship.'})
        else:
            rows.append({'claim':skill,'tested':'No matching test','result':'Not tested','passed':0,'total':0,'scope':'No conclusion can be drawn about this skill.'})
    return rows


def solution_replay(state):
    entries=[]
    for i,t in enumerate(state['tasks']):
        previous=t['starter']
        entries.append({'at':state.get('started_at') or 0,'tier':i,'kind':'start','label':'Starting code','files':previous,'changes':{}})
        for r in t.get('runs',[]):
            files=r.get('tested_files')
            if not files:continue
            diff={f:'\n'.join(difflib.unified_diff(previous.get(f,'').splitlines(),code.splitlines(),fromfile='Before',tofile='After',lineterm='')) for f,code in files.items() if previous.get(f)!=code}
            entries.append({'at':r['at'],'tier':i,'kind':'test','label':f'{r.get("question_passed",r["passed"])}/{r.get("question_total",r["total"])} example tests passed',
                'files':files,'changes':diff,'question':r.get('question','both')})
            previous=files
        for e in state.get('events',[]):
            if e.get('tier')==i and e['kind']=='hint':
                entries.append({'at':e['at'],'tier':i,'kind':'hint','label':e['detail'],'files':{},'changes':{}})
        if state.get('status')=='completed' or t.get('final'):
            entries.append({'at':state.get('finished_at',state.get('deadline',0)),'tier':i,'kind':'submit','label':'Final saved code','files':t['files'],
                'changes':{f:'\n'.join(difflib.unified_diff(previous.get(f,'').splitlines(),code.splitlines(),fromfile='Before',tofile='After',lineterm='')) for f,code in t['files'].items() if previous.get(f)!=code}})
    return sorted(entries,key=lambda x:x['at'])
