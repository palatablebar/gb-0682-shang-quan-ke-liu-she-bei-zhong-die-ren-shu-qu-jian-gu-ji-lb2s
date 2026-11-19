import base64,hashlib
from datetime import datetime
from app.errors import DomainError
from app.modules.m2_mapping.coerce import instant,SENTINELS
from app.modules.m3_versions.rules import ALIASES
from app.modules.m4_segments.rules import model
from app.modules.m5_calculate.rules import bounds

ZONE_NAMES={'A':'东门','B':'中庭','C':'西门'}
MAX_COUNT=1_000_000

def hour_bucket(value):
    # 与M3一致的UTC小时桶归整
    return datetime.fromisoformat(instant(str(value))).replace(minute=0,second=0,microsecond=0).isoformat()

def _zone(value):
    text=str(value).strip();return ALIASES.get(text,text)

def _as_int(value):
    try:return int(str(value))
    except (TypeError,ValueError):return None

def _fmt(value):
    return int(value) if float(value)==int(value) else round(float(value),1)

def _bucket_observations(state,bucket):
    return [o for o in state.get('observations',[]) if o.get('bucket')==bucket]

def _device_cap(state,zone):
    counts=[int(o['count']) for o in state.get('observations',[]) if o.get('kind')=='zone' and o.get('left')==zone]
    if not counts:counts=[int(o['count']) for o in state.get('observations',[]) if o.get('kind')=='zone']
    return max(counts) if counts else 0

def _interval(state,bucket,zone):
    # 缺测读数在可行范围内变动时，联合人数区间并集；该时段已有有效读数时返回None
    obs=_bucket_observations(state,bucket)
    if any(o.get('kind')=='zone' and o.get('left')==zone for o in obs):return None
    floor=max([int(o['count']) for o in obs if o.get('kind')=='pair' and zone in (o.get('left'),o.get('right'))]or[0])
    cap=max(_device_cap(state,zone),floor);mid=(floor+cap)//2
    found=[]
    for x in sorted({floor,mid,cap}):
        trial=obs+[{'kind':'zone','left':zone,'right':'','count':x}]
        try:
            low,high,_,_=bounds(model(trial));found.append((x,low,high))
        except DomainError:continue
    if found:
        low=min(l for _,l,_ in found);high=max(h for _,_,h in found)
        residual=next((h-l for x,l,h in found if x==mid),min(h-l for _,l,h in found))
        return low,high,residual
    others=[int(o['count']) for o in obs if o.get('kind')=='zone']
    return max(others+[floor]) if others else floor,sum(others)+cap,0

def derive_tasks(state):
    # 复核任务按(设备,UTC小时桶)唯一确定，同一批数据重跑得到同一任务
    tasks=[];seen=set()
    for q in state.get('quarantine',[]):
        raw=q.get('raw') or {}
        if not isinstance(raw,dict):continue
        if q.get('code')!='MISSING' or raw.get('kind')!='zone':continue
        if str(raw.get('count','')).strip().upper() not in SENTINELS:continue
        zone=_zone(raw.get('left',''))
        if zone not in ZONE_NAMES:continue
        try:bucket=hour_bucket(raw.get('bucket',''))
        except DomainError:continue
        if (bucket,zone) in seen:continue
        seen.add((bucket,zone))
        supplemented=any(o.get('kind')=='zone' and o.get('left')==zone and str(o.get('id','')).startswith('SUP') for o in _bucket_observations(state,bucket))
        interval=_interval(state,bucket,zone)
        if interval is None:
            calc=next((c for c in state.get('calculations',[]) if c.get('id')==bucket),None)
            existing=next((int(o['count']) for o in _bucket_observations(state,bucket) if o.get('kind')=='zone' and o.get('left')==zone),0)
            exposure=(calc['lower']+calc['upper'])/2 if calc else existing
            uncertainty=narrowing=0;note='；该时段已有有效观测，无收窄收益'
        else:
            low,high,residual=interval;uncertainty=high-low;narrowing=max(0,uncertainty-residual)
            exposure=(low+high)/2;note=''
        tasks.append({'task_id':hashlib.sha256(f'{bucket}|{zone}'.encode()).hexdigest()[:16],
            'bucket':bucket,'zone':zone,'zone_name':ZONE_NAMES[zone],'line':q.get('line'),
            'status':'已补录' if supplemented else '待复核','_e':exposure,'_u':uncertainty,'_w':narrowing,
            '_score':narrowing*exposure,'_note':note})
    # 裁决：按预期可确认客次(可收窄W×客流暴露E)降序，同分依次按E、U、设备、时段
    tasks.sort(key=lambda t:(-t['_score'],-t['_e'],-t['_u'],t['zone'],t['bucket']))
    total=len(tasks)
    for rank,t in enumerate(tasks,1):
        t['exposure']=_fmt(t.pop('_e'));t['uncertainty']=_fmt(t.pop('_u'));t['narrowing']=_fmt(t.pop('_w'))
        t['score']=_fmt(t.pop('_score'));t['rank']=rank
        t['reason']=f"可收窄{t['narrowing']}客次×客流暴露{t['exposure']}客次=得分{t['score']}，按预期可确认客次第{rank}位/共{total}台{t.pop('_note')}"
    return tasks

def next_version(state,bucket,zone):
    versions=[0]
    for o in state.get('observations',[]):
        if o.get('kind')=='zone' and o.get('left')==zone and o.get('bucket')==bucket:versions.append(_as_int(o.get('version')) or 0)
    for q in state.get('quarantine',[]):
        raw=q.get('raw') or {}
        if not isinstance(raw,dict):continue
        try:
            if raw.get('kind')=='zone' and _zone(raw.get('left',''))==zone and hour_bucket(raw.get('bucket',''))==bucket:
                versions.append(_as_int(raw.get('version')) or 0)
        except DomainError:continue
    return max(versions)+1

def parse_supplement(data):
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    zone=_zone(data.get('zone',''))
    if zone not in ZONE_NAMES:raise DomainError('ZONE','区域标识无效')
    bucket=hour_bucket(data.get('bucket',''))
    count=data.get('count')
    if isinstance(count,bool) or not isinstance(count,int) or not 0<=count<=MAX_COUNT:
        raise DomainError('RANGE','补录人数必须为0至1000000的整数')
    return bucket,zone,count

def supplement_content(state,bucket,zone,count):
    if not any(t['bucket']==bucket and t['zone']==zone for t in state.get('review_tasks',[])):
        raise DomainError('REFERENCE','该时段设备无缺测复核任务')
    version=next_version(state,bucket,zone)
    task_id=hashlib.sha256(f'{bucket}|{zone}'.encode()).hexdigest()[:16]
    line=f"SUP{task_id[:8]},{bucket},{version},zone,{zone},,{count},人,补录\n"
    source=base64.b64decode(state['source_b64'])
    if not source.endswith(b'\n'):source+=b'\n'
    encoding='gb18030' if state.get('encoding')=='gb18030' else 'utf-8'
    return source+line.encode(encoding),version

def merge_scope(old,new,affected):
    # 只重判受影响时段：其余时段沿用旧结论；校验失败则整体采用新结果
    keys=(('observations','bucket'),('models','bucket'),('calculations','id'))
    def group(state,key,idkey):
        grouped={}
        for e in state.get(key,[]):grouped.setdefault(e.get(idkey),[]).append(e)
        return grouped
    consistent=True
    for key,idkey in keys:
        og,ng=group(old,key,idkey),group(new,key,idkey)
        for bucket in set(og)|set(ng):
            if bucket not in affected and og.get(bucket)!=ng.get(bucket):consistent=False;break
        if not consistent:break
    if not consistent:
        merged=dict(new)
        merged['rejudged']={'buckets':sorted({c.get('id') for c in new.get('calculations',[])}|set(affected)),'carried_over':[]}
        return merged
    merged=dict(new)
    for key,idkey in keys:
        queues={b:list(v) for b,v in group(old,key,idkey).items()}
        merged[key]=[e if e.get(idkey) in affected else queues[e.get(idkey)].pop(0) for e in new.get(key,[])]
    fresh={(q.get('line'),q.get('code'),q.get('id')) for q in new.get('quarantine',[])}
    merged['superseded']=[dict(q,superseded=True) for q in old.get('quarantine',[]) if (q.get('line'),q.get('code'),q.get('id')) not in fresh]
    carried=sorted({e.get(idkey) for key,idkey in keys for e in old.get(key,[])}-set(affected))
    merged['rejudged']={'buckets':sorted(affected),'carried_over':carried}
    return merged
