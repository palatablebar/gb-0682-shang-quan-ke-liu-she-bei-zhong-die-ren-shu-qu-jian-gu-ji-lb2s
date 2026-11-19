from app.modules.m7_archive.export_out import checksum
from app.modules.m7_archive.review import derive_tasks

def execute(state):
    state['review_tasks']=derive_tasks(state)
    state['archive']={'sha256':checksum(state),'evidence_count':len(state.get('calculations',[]))+len(state['quarantine']),
        'ready':state.get('reconcile',{}).get('balanced',False)}
    return state
