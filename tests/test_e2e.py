import csv,io
from app.pipeline import execute,load_sample
from app import store
HEADER="scan_id,parcel,node,event_time,received_time,destination,promised_hours,declared_value,reported,note"
REF="""R01,R1,收件,2026-11-02T00:00:00Z,2026-11-02T01:00:00Z,JP,24,200,0,参考
R02,R1,起运,2026-11-02T12:00:00Z,2026-11-02T13:00:00Z,JP,24,200,0,参考
R03,R1,清关,2026-11-03T00:00:00Z,2026-11-03T01:00:00Z,JP,24,200,0,参考
R04,R1,妥投,2026-11-04T00:00:00Z,2026-11-04T02:00:00Z,JP,24,200,0,参考"""
BASE=HEADER+"\n"+REF+"\n"+"\n".join([
"X01,X9,收件,2026-11-16T00:00:00Z,2026-11-16T01:00:00Z,JP,24,200,0,部分扫描",
"X02,X9,起运,2026-11-16T12:00:00Z,2026-11-16T13:00:00Z,JP,24,200,0,部分扫描"])
RESCAN=BASE+"\n"+"X03,X9,妥投,2026-11-16T12:00:00Z,2026-11-17T00:00:00Z,JP,24,200,0,补传与起运同刻"
def risk_of(run,id): return next(r for r in run["stages"][4]["rows"] if r["parcel"]==id)
def test_full_sample_flow(client):
    r=client.post("/api/runs",json={"sample":"dirty.csv"});assert r.status_code==200
    p=r.get_json();assert len(p["stages"])==7
    assert p["stages"][5]["metrics"]["balanced"] is True
def test_idempotent_real_input(sample):
    a=execute(sample);b=execute(sample)
    assert a["run_id"]==b["run_id"] and len(store.history())==1
def test_stale_review_409(client):
    "TRAP-26 陈旧复核不得覆盖新版本"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json();u="/api/runs/"+p["run_id"]+"/review"
    assert client.post(u,json={"version":1,"decision":"已复核"}).status_code==200
    assert client.post(u,json={"version":1,"decision":"退回复核"}).status_code==409
def test_export_has_manifest_and_quarantine(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert rows[0][1]=="input_sha256" and any(x[3]=="quarantine" for x in rows[1:])
def test_failed_import_is_atomic(client):
    assert client.post("/api/runs",json={"csv":"bad"}).status_code==422
    assert store.history()==[]
def test_module_entrances(client):
    from app.config import MODULES
    for slug,name in MODULES:
        assert client.get("/api/"+slug).status_code==200
        assert client.get("/"+slug).status_code==200
def test_missing_run_404(client): assert client.get("/api/runs/missing").status_code==404
def test_rescan_moves_high_risk_to_insufficient(client):
    "补传矛盾扫描后同一票从高风险变为证据不足，赔付预估与导出沿用新结论"
    before=client.post("/api/runs",json={"csv":BASE}).get_json()
    assert risk_of(before,"X9")["risk"]=="未妥投·高风险"
    after=client.post("/api/runs",json={"csv":RESCAN}).get_json()
    assert after["run_id"]!=before["run_id"]
    row=risk_of(after,"X9")
    assert row["risk"]=="证据不足" and row["expected"]=="0.00"
    book=next(r for r in after["stages"][5]["rows"] if r["parcel"]=="X9")
    assert book["status"]=="证据不足"
    text=client.get("/api/runs/"+after["run_id"]+"/archive").data.decode("utf-8-sig")
    assert "证据不足" in text and "SIMULTANEOUS_CONFLICT" in text
def test_same_rescan_adds_no_new_conclusion(client):
    "同一补传重跑复用既有批次，结论不新增、不变化"
    a=client.post("/api/runs",json={"csv":RESCAN}).get_json()
    b=client.post("/api/runs",json={"csv":RESCAN}).get_json()
    assert a["run_id"]==b["run_id"] and len(store.history())==1
    assert risk_of(a,"X9")==risk_of(b,"X9")
def test_failed_replace_keeps_previous(client,monkeypatch):
    "保存失败时旧批次原样保留"
    first=client.post("/api/runs",json={"sample":"clean.csv"}).get_json()
    real=store.connect
    monkeypatch.setattr(store,"connect",lambda:(_ for _ in ()).throw(RuntimeError("db down")))
    assert client.post("/api/runs",json={"sample":"dirty.csv"}).status_code==500
    monkeypatch.setattr(store,"connect",real)
    assert [h["run_id"] for h in store.history()]==[first["run_id"]]
    assert store.get(first["run_id"])["stages"][4]["rows"]
def test_rerun_never_overwrites_reviewed(client):
    "同源重跑命中既有批次，已复核状态与旧版结论保留"
    p=client.post("/api/runs",json={"sample":"partial.csv"}).get_json()
    assert client.post("/api/runs/"+p["run_id"]+"/review",json={"version":1,"decision":"已复核"}).status_code==200
    again=client.post("/api/runs",json={"sample":"partial.csv"}).get_json()
    assert again["run_id"]==p["run_id"] and again["decision"]=="已复核"
def test_original_scans_survive_later_runs(client):
    "后续批次不清空历史批次的原始扫描与隔离行"
    old=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    client.post("/api/runs",json={"sample":"partial.csv"})
    text=client.get("/api/runs/"+old["run_id"]+"/archive").data.decode("utf-8-sig")
    assert "0093" in text and "PATH_REGRESSION" in text and len(store.history())==2
def test_timeliness_page_explains_evidence_and_reference(client):
    client.post("/api/runs",json={"sample":"partial.csv"})
    html=client.get("/timeliness").data.decode()
    for term in ("本票证据","参考依据","缺口节点","证据不足","不作为已发生事实"): assert term in html
