from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from decimal import Decimal
from app.values import instant,number,money
from app.errors import DomainError
HOLIDAYS={"JP":{"2026-11-03","2026-11-23"},"US":{"2026-11-26"},"DE":{"2026-10-03"}}
ORDER=["ACCEPT","DEPART","CUSTOMS","DELIVERED"]
def working_hours(start,end,zone,holidays):
    if end<start:raise DomainError("NEGATIVE_INTERVAL","妥投早于收件")
    tz=ZoneInfo(zone);cursor=start;seconds=Decimal(0)
    while cursor<end:
        local=cursor.astimezone(tz);next_day=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=tz).astimezone(cursor.tzinfo)
        stop=min(end,next_day)
        if local.weekday()<5 and local.date().isoformat() not in holidays:seconds+=Decimal(str((stop-cursor).total_seconds()))
        cursor=stop
    return seconds/3600
def median(values):
    ordered=sorted(values);n=len(ordered);mid=n//2
    return ordered[mid] if n%2 else (ordered[mid-1]+ordered[mid])/2
def reference_table(rows,holidays_of):
    "同目的地同节点对的已妥投分段工作小时中位数；仅作剩余估算参考，不产生事实"
    segments={}
    for row in rows:
        if not row.get("delivered_at"):continue
        times=row.get("node_times",{});present=[n for n in ORDER if n in times]
        for i,a in enumerate(present):
            for b in present[i+1:]:
                hours=working_hours(instant(times[a]),instant(times[b]),row["zone"],holidays_of(row["destination"]))
                segments.setdefault((row["destination"],f"{a}→{b}"),[]).append(hours)
    return {key:{"hours":median(v),"samples":len(v),"source":"本批"} for key,v in segments.items()}
def parameter_reference(raw):
    "parameters.reference 显式传入的历史耗时表，进入批次摘要，重跑结论一致"
    table={}
    for destination,pairs in (raw or {}).items():
        if not isinstance(pairs,dict):raise DomainError("BAD_REFERENCE","参考表需要按目的地组织")
        for segment,hours in pairs.items():
            table[(destination,segment)]={"hours":number(hours),"samples":0,"source":"参数"}
    return table
def remaining_estimate(destination,last_node,reference):
    "优先直达段，其次沿标准序列逐段求和；任一段无参考则整体无参考"
    if last_node=="DELIVERED":return [],Decimal(0)
    direct=reference.get((destination,f"{last_node}→DELIVERED"))
    if direct:return [{"segment":f"{last_node}→DELIVERED",**direct}],direct["hours"]
    used=[];total=Decimal(0);start=ORDER.index(last_node)
    for i in range(start,len(ORDER)-1):
        segment=f"{ORDER[i]}→{ORDER[i+1]}"
        ref=reference.get((destination,segment))
        if not ref:return None,None
        used.append({"segment":segment,**ref});total+=ref["hours"]
    return used,total
def adjudicate(row,elapsed,promised,reference,quarantined):
    "唯一风险结论：事实层只含已发生扫描；缺口仅以参考估算身份进入；证据矛盾不出具等级"
    if row.get("delivered_at"):
        return {"risk":"已妥投·超时" if elapsed>promised else "已妥投·达标","risk_basis":"按已发生的妥投事实判定，缺口节点不参与计时","reference":[],"remaining_est_hours":None}
    if quarantined:
        reasons="、".join(sorted({q.get("reason","?") for q in quarantined}))
        return {"risk":"证据不足","risk_basis":f"本票有{len(quarantined)}条扫描被隔离（{reasons}），证据链存在未决矛盾，不出具风险等级，不自动计赔","reference":[],"remaining_est_hours":None}
    times=row.get("node_times",{});last_node=row.get("last_node") or next((n for n in reversed(ORDER) if n in times),"ACCEPT")
    used,remaining=remaining_estimate(row.get("destination"),last_node,reference)
    if remaining is None:
        level="未妥投·中风险" if elapsed>promised*Decimal("0.8") else "未妥投·低风险"
        basis=f"无同目的地同节点参考，仅按已发生事实：已计时{elapsed}h / 承诺{promised}h"
    else:
        projected=elapsed+remaining
        segments="、".join(f"{u['segment']} {u['hours']}h（{u['source']}{u['samples']}票）" if u["samples"] else f"{u['segment']} {u['hours']}h（{u['source']}参考）" for u in used)
        basis=f"已计时{elapsed}h + 参考剩余{remaining}h（{segments}），预计{projected}h / 承诺{promised}h"
        level="未妥投·高风险" if projected>promised else "未妥投·中风险" if projected>promised*Decimal("0.8") else "未妥投·低风险"
    if elapsed>=promised:level="未妥投·高风险";basis=f"已发生工作计时{elapsed}h ≥ 承诺{promised}h，已超时；"+basis
    return {"risk":level,"risk_basis":basis,"reference":[{**u,"hours":str(u["hours"])} for u in used or []],"remaining_est_hours":str(remaining) if remaining is not None else None}
def evaluate(rows,context):
    p=context.get("parameters",{});as_of=instant(p.get("as_of","2026-11-18T00:00:00Z"));rate=number(p.get("hourly_compensation","1.00"))
    if rate<0:raise DomainError("BAD_RATE","赔付费率不能为负")
    holidays_of=lambda destination:set(p.get("holidays",{}).get(destination,HOLIDAYS.get(destination,set())))
    reference={**reference_table(rows,holidays_of),**parameter_reference(p.get("reference"))}
    quarantined={}
    for q in context.get("quarantine",[]):
        if q.get("parcel"):quarantined.setdefault(q["parcel"],[]).append(q)
    good=[];insufficient=high=0
    for row in rows:
        start=instant(row["accepted_at"]);end=instant(row["delivered_at"]) if row.get("delivered_at") else max(start,as_of)
        hours=working_hours(start,end,row["zone"],holidays_of(row["destination"]))
        promised=number(row["promised_hours"]);late=max(Decimal(0),hours-promised);comp=min(number(row["declared_value"])*Decimal("0.10"),late*rate)
        verdict=adjudicate(row,hours,promised,reference,quarantined.get(row.get("parcel"),[]))
        if verdict["risk"]=="证据不足":comp=Decimal(0);insufficient+=1
        if verdict["risk"]=="未妥投·高风险":high+=1
        good.append({**row,"working_hours":str(hours),"late_hours":str(late),"expected":money(comp),"sla":"超时" if late>0 else "达标","clock_end":end.isoformat(),**verdict})
    return {"rows":good,"quarantine":[],"metrics":{"parcels":len(good),"late_parcels":sum(r["sla"]=="超时" for r in good),"compensation":money(sum((number(r["expected"]) for r in good),Decimal(0))),"high_risk":high,"insufficient":insufficient}}
