from app.modules.m5_timeliness.rules import working_hours,evaluate
from app.pipeline import calculate,load_sample
from app.values import instant
from decimal import Decimal
import pytest
from app.errors import DomainError
def partial(): return calculate(load_sample("partial.csv"))["stages"][4]
def parcel(result,id): return next(r for r in result["rows"] if r["parcel"]==id)
def test_real_holiday_excluded(result): assert result["stages"][4]["rows"][0]["working_hours"]=="24.0"
def test_real_sla_uses_event_not_upload(result): assert result["stages"][4]["metrics"]["compensation"]=="0.00"
def test_holiday_full_day_stops():
    "TRAP-23"
    assert working_hours(instant("2026-11-02T15:00:00Z"),instant("2026-11-03T15:00:00Z"),"Asia/Tokyo",{"2026-11-03"})==0
def test_weekend_stops(): assert working_hours(instant("2026-11-06T15:00:00Z"),instant("2026-11-08T15:00:00Z"),"Asia/Tokyo",set())==0
def test_reversed_interval_rejected():
    with pytest.raises(DomainError):working_hours(instant("2026-11-04T00:00:00Z"),instant("2026-11-02T00:00:00Z"),"Asia/Tokyo",set())
def test_compensation_capped():
    row={"accepted_at":"2026-11-02T00:00:00Z","delivered_at":"2026-11-06T00:00:00Z","zone":"Asia/Tokyo","destination":"JP","promised_hours":"0","declared_value":"100"}
    assert evaluate([row],{})["rows"][0]["expected"]=="10.00"
def test_dst_actual_elapsed_hours():
    "TRAP-12"
    assert working_hours(instant("2026-11-01T04:00:00Z"),instant("2026-11-02T05:00:00Z"),"America/New_York",set())==0
def test_gap_never_treated_as_occurred():
    "缺口节点只进gaps与参考估算，本票证据不含未出现节点"
    row=parcel(partial(),"P300")
    assert row["gaps"]==["DEPART","DELIVERED"] and set(row["node_times"])=={"ACCEPT","CUSTOMS"}
def test_reference_same_destination_same_segment():
    "参考依据来自同目的地同节点对的已妥投工作小时中位数"
    ref=parcel(partial(),"P100")["reference"]
    assert ref==[{"segment":"ACCEPT→DELIVERED","hours":"24.0","samples":3,"source":"本批"}]
def test_risk_levels():
    m=partial()
    assert parcel(m,"P100")["risk"]=="未妥投·低风险"
    assert parcel(m,"P600")["risk"]=="未妥投·中风险"
    assert parcel(m,"P200")["risk"]=="未妥投·高风险" and parcel(m,"P200")["expected"]=="8.00"
    assert parcel(m,"P300")["risk"]=="未妥投·高风险" and "预计33.0h" in parcel(m,"P300")["risk_basis"]
def test_quarantined_scan_downgrades_to_insufficient():
    "补传带来的矛盾扫描使未妥投票降级为证据不足且不计赔"
    row=parcel(partial(),"P400")
    assert row["risk"]=="证据不足" and row["expected"]=="0.00" and "SIMULTANEOUS_CONFLICT" in row["risk_basis"]
def test_no_reference_falls_back_to_facts():
    row=parcel(partial(),"P500")
    assert row["risk"]=="未妥投·低风险" and row["reference"]==[] and "无同目的地同节点参考" in row["risk_basis"]
def test_delivered_parcel_fact_decides(result):
    "已妥投票事实链完整，被隔离扫描不改变结论"
    row=parcel(result["stages"][4],"0001")
    assert row["risk"]=="已妥投·达标" and row["delivered_at"]=="2026-11-04T00:00:00+00:00"
def test_parameter_reference_is_deterministic():
    "显式历史参考进入批次摘要：同参重跑同批次，改参换批次"
    raw=load_sample("partial.csv");params={"reference":{"JP":{"ACCEPT→DELIVERED":"100"}}}
    a=calculate(raw,params);b=calculate(raw,params);c=calculate(raw)
    assert a["run_id"]==b["run_id"] and a["run_id"]!=c["run_id"]
    row=parcel(a["stages"][4],"P100")
    assert row["reference"][0]["source"]=="参数" and row["remaining_est_hours"]=="100"
def test_bad_reference_rejected():
    with pytest.raises(DomainError):calculate(load_sample("partial.csv"),{"reference":{"JP":"oops"}})
