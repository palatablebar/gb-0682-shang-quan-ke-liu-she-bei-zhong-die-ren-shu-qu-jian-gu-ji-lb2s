import csv,io,json
from app import store
from app.modules.m7_archive.export_out import checksum

B09='2026-11-16T09:00:00+00:00'
B10='2026-11-16T10:00:00+00:00'
B11='2026-11-16T11:00:00+00:00'

# 两台缺测设备：A@10 客流中等但可收窄巨大；B@11 客流极高但可收窄有限
ORDER_CSV='''id,bucket,version,kind,left,right,count,unit,note
1,2026-11-16T09:00:00Z,1,zone,A,,1000,人,峰值
2,2026-11-16T09:00:00Z,1,zone,B,,100,人,x
3,2026-11-16T09:00:00Z,1,zone,C,,90,人,x
4,2026-11-16T09:00:00Z,1,pair,A,B,40,人,x
5,2026-11-16T09:00:00Z,1,pair,A,C,30,人,x
6,2026-11-16T09:00:00Z,1,pair,B,C,20,人,x
7,2026-11-16T10:00:00Z,1,zone,A,,N/A,人,缺测
8,2026-11-16T10:00:00Z,1,zone,B,,100,人,x
9,2026-11-16T10:00:00Z,1,zone,C,,90,人,x
10,2026-11-16T10:00:00Z,1,pair,A,B,40,人,x
11,2026-11-16T10:00:00Z,1,pair,A,C,30,人,x
12,2026-11-16T10:00:00Z,1,pair,B,C,20,人,x
13,2026-11-16T11:00:00Z,1,zone,A,,2000,人,x
14,2026-11-16T11:00:00Z,1,zone,B,,N/A,人,缺测
15,2026-11-16T11:00:00Z,1,zone,C,,1800,人,x
16,2026-11-16T11:00:00Z,1,pair,A,B,50,人,x
17,2026-11-16T11:00:00Z,1,pair,A,C,900,人,x
18,2026-11-16T11:00:00Z,1,pair,B,C,40,人,x
'''

# 10点时段A缺测导致整桶隔离，补录后应只重判该时段
GAP_CSV='''id,bucket,version,kind,left,right,count,unit,note
1,2026-11-16T09:00:00Z,1,zone,A,,120,人,x
2,2026-11-16T09:00:00Z,1,zone,B,,110,人,x
3,2026-11-16T09:00:00Z,1,zone,C,,90,人,x
4,2026-11-16T09:00:00Z,1,pair,A,B,40,人,x
5,2026-11-16T09:00:00Z,1,pair,A,C,30,人,x
6,2026-11-16T09:00:00Z,1,pair,B,C,20,人,x
7,2026-11-16T10:00:00Z,1,zone,A,,N/A,人,缺测
8,2026-11-16T10:00:00Z,1,zone,B,,111,人,x
9,2026-11-16T10:00:00Z,1,zone,C,,91,人,x
10,2026-11-16T10:00:00Z,1,pair,A,B,41,人,x
11,2026-11-16T10:00:00Z,1,pair,A,C,31,人,x
12,2026-11-16T10:00:00Z,1,pair,B,C,21,人,x
'''

def post_run(client,csv_text):
    r=client.post('/api/runs',json={'csv':csv_text});assert r.status_code==200
    return r.get_json()

def supplement(client,run_id,count,bucket=B10,zone='A'):
    return client.post(f'/api/runs/{run_id}/supplement',json={'bucket':bucket,'zone':zone,'count':count})

def test_order_by_expected_confirmed_customers(client):
    # 裁决：按预期可确认客次(可收窄×客流暴露)降序，而非单一客流量或不确定度
    tasks=post_run(client,ORDER_CSV)['review_tasks']
    assert [(t['zone'],t['bucket']) for t in tasks]==[('A',B10),('B',B11)]
    high_flow,= [t for t in tasks if t['zone']=='B']
    big_gain,= [t for t in tasks if t['zone']=='A']
    assert high_flow['exposure']>big_gain['exposure']  # 纯按客流量会先查B
    assert big_gain['narrowing']>high_flow['narrowing']  # 纯按不确定度会先查A
    assert big_gain['score']>high_flow['score']  # 裁决得分让收窄收益大者优先
    assert [t['rank'] for t in tasks]==[1,2]
    assert '可收窄' in big_gain['reason'] and '客流暴露' in big_gain['reason']

def test_covered_missing_scores_zero(state):
    # 脏样例中缺测时段另有有效观测，补录无收窄收益
    tasks=state['review_tasks']
    assert len(tasks)==1 and tasks[0]['zone']=='A' and tasks[0]['status']=='待复核'
    assert tasks[0]['score']==0 and tasks[0]['narrowing']==0
    assert '已有有效观测' in tasks[0]['reason']

def test_page_and_export_share_order(client):
    run=post_run(client,ORDER_CSV)
    page=client.get('/m7_archive').data.decode()
    assert '缺测设备复核清单' in page
    assert page.index('data-zone="A"')<page.index('data-zone="B"')  # 页面与裁决同序
    text=client.get(f"/api/archive/{run['run_id']}/export.csv").data.decode('utf-8-sig')
    rows=[r for r in csv.reader(io.StringIO(text)) if r and r[0]=='复核任务']
    assert [r[3] for r in rows]==['A','B']  # 导出与页面同序
    evidence=[json.loads(r[5]) for r in rows]
    assert [e['rank'] for e in evidence]==[1,2]
    assert all('可收窄' in e['reason'] and '客流暴露' in e['reason'] for e in evidence)  # 导出引用入选理由

def test_supplement_rejudges_only_affected_bucket(client):
    old=post_run(client,GAP_CSV)
    old_calc={c['id']:c for c in old['calculations']}
    assert B10 not in old_calc  # 缺测时段被隔离
    r=supplement(client,old['run_id'],120);assert r.status_code==200
    new=r.get_json()
    assert new['rejudged']['buckets']==[B10]
    assert new['rejudged']['carried_over']==[B09]
    new_calc={c['id']:c for c in new['calculations']}
    assert new_calc[B09]==old_calc[B09]  # 无关时段结论原样保留
    assert (new_calc[B10]['lower'],new_calc[B10]['upper'])==(229,250)  # 受影响时段重判

def test_supplement_keeps_old_records(client):
    old=post_run(client,GAP_CSV)
    new=supplement(client,old['run_id'],120).get_json()
    kept=client.get(f"/api/runs/{old['run_id']}").get_json()
    assert B10 not in {c['id'] for c in kept['calculations']}  # 旧批次原样保留
    assert any(q['code']=='MISSING' for q in new['quarantine'])  # 缺测原记录不丢失
    assert len(new['superseded'])==5 and all(s['superseded'] for s in new['superseded'])  # 被取代的隔离记录留存
    assert checksum(new)==new['archive']['sha256']  # 合并后证据指纹自洽

def test_same_batch_creates_no_new_tasks(client):
    post_run(client,GAP_CSV)
    before=len(store.tasks())
    post_run(client,GAP_CSV);post_run(client,GAP_CSV)
    assert len(store.tasks())==before  # 同一批数据重跑不新增复核任务
    rid=post_run(client,GAP_CSV)['run_id']
    a=supplement(client,rid,120).get_json();b=supplement(client,rid,120).get_json()
    assert a['run_id']==b['run_id']  # 同一补录幂等
    assert len(store.tasks())==before  # 补录不新增任务，只更新原任务
    task,=[t for t in store.tasks() if t['bucket']==B10]
    assert task['status']=='已补录' and task['run_id']==a['run_id']

def test_failed_switch_keeps_old_version(client):
    rid=post_run(client,GAP_CSV)['run_id']
    runs_before=len(client.get('/api/runs').get_json())
    cases=[({'bucket':B10,'zone':'A','count':-1},'RANGE'),
        ({'bucket':B10,'zone':'A','count':2000000},'RANGE'),
        ({'bucket':B10,'zone':'A','count':'120'},'RANGE'),
        ({'bucket':'not-a-time','zone':'A','count':1},'DATE'),
        ({'bucket':B10,'zone':'X','count':1},'ZONE'),
        ({'bucket':'2026-11-16T12:00:00+00:00','zone':'A','count':1},'REFERENCE')]
    for payload,code in cases:
        r=client.post(f'/api/runs/{rid}/supplement',json=payload)
        assert r.status_code==422 and r.get_json()['code']==code
        assert client.get('/api/runs').get_json()[0]['id']==rid  # 旧版仍是当前批次
    assert len(client.get('/api/runs').get_json())==runs_before  # 未写入半成品
    assert client.post('/api/runs/absent/supplement',json={'bucket':B10,'zone':'A','count':1}).status_code==404
