"""Server-owned checks; expected values never come from the authoring model."""
import random

def add_project_checks(task):
    if task.get('contract_version')!=2:return
    rng=random.SystemRandom()
    tier=['Novice','Adept','Master'].index(task['tier'])
    for i in range(12):
        events=[]
        for j in range(rng.randrange(1,40)):
            event={'id':str(rng.randrange(12)), 'type':rng.choice(['join','view','leave'])}
            if tier==0:
                if rng.random()>.2:event['active']=rng.choice([True,False,0,1,None])
            elif rng.random()>.2:event['value']=rng.randrange(-20,21)
            events.append(event)
        if tier==0:expected=[e['id'] for e in events if e.get('active') is True]
        else:
            accepted=[];seen=set()
            for e in events:
                if tier==2 and e['id'] in seen:continue
                seen.add(e['id']);accepted.append(e)
            expected={kind:sum(e.get('value',0) for e in accepted if e['type']==kind) for kind in {e['type'] for e in accepted}}
            expected={k:v for k,v in expected.items() if v}
        task['cases'].append({'name':f'Independent project check {i+1}','module':'task_api.main','input':{'events':events},'expected':expected,'public':False})
    task['independent_checks']=True
