# The shared module owns the same objects the dev root used to own.
# These moves keep the live addresses. A plan that destroys or replaces one of
# them is wrong.
moved {
  from = module.data_bucket
  to   = module.workload.module.data_bucket
}

moved {
  from = module.ledger_table
  to   = module.workload.module.ledger_table
}

moved {
  from = module.state_table
  to   = module.workload.module.state_table
}

moved {
  from = module.counters_table
  to   = module.workload.module.counters_table
}

moved {
  from = module.flag_execution_mode
  to   = module.workload.module.flag_execution_mode
}

moved {
  from = module.flag_trading_enabled
  to   = module.workload.module.flag_trading_enabled
}

moved {
  from = module.flag_kill_switch
  to   = module.workload.module.flag_kill_switch
}

moved {
  from = module.alerts_topic
  to   = module.workload.module.alerts_topic
}

moved {
  from = module.alarm_pipeline_failed
  to   = module.workload.module.alarm_pipeline_failed
}

moved {
  from = module.alarm_pipeline_missed
  to   = module.workload.module.alarm_pipeline_missed
}

moved {
  from = module.pipeline_lambda_role
  to   = module.workload.module.pipeline_lambda_role
}

moved {
  from = module.lambda
  to   = module.workload.module.lambda
}

moved {
  from = module.state_machine_role
  to   = module.workload.module.state_machine_role
}

moved {
  from = module.scan_state_machine
  to   = module.workload.module.scan_state_machine
}

moved {
  from = module.scheduler_role
  to   = module.workload.module.scheduler_role
}

moved {
  from = module.scan_schedule
  to   = module.workload.module.scan_schedule
}

moved {
  from = module.market_probe_role
  to   = module.workload.module.market_probe_role
}

moved {
  from = module.market_probe_lambda
  to   = module.workload.module.market_probe_lambda
}

moved {
  from = module.market_probe_scheduler_role
  to   = module.workload.module.market_probe_scheduler_role
}

moved {
  from = module.market_probe_schedule
  to   = module.workload.module.market_probe_schedule
}

moved {
  from = module.alarm_market_geo_blocked
  to   = module.workload.module.alarm_market_geo_blocked
}

moved {
  from = module.alarm_market_probe_missed
  to   = module.workload.module.alarm_market_probe_missed
}

moved {
  from = module.recorders_role
  to   = module.workload.module.recorders_role
}

moved {
  from = module.recorders_lambda
  to   = module.workload.module.recorders_lambda
}

moved {
  from = module.recorders_scheduler_role
  to   = module.workload.module.recorders_scheduler_role
}

moved {
  from = module.recorders_schedule
  to   = module.workload.module.recorders_schedule
}

moved {
  from = module.alarm_recorders_missed
  to   = module.workload.module.alarm_recorders_missed
}
