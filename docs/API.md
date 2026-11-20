# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

时效计算接受parameters.reference传入同目的地同节点历史工作小时表，与as_of、holidays一样进入批次摘要；同一输入与参数重跑命中既有批次，不新增结论，保存失败时旧批次保留。时效行携带risk、risk_basis、gaps、reference，赔付对账与归档导出沿用同一结论。
