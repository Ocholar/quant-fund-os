do $$
declare
    t text;
    reset_tables text[] := array[
        'trades',
        'positions',
        'portfolio_snapshots',
        'symbol_quarantine',
        'strategy_performance',
        'strategy_scores',
        'strategy_metrics',
        'trade_metrics',
        'performance_metrics',
        'allocator_memory',
        'allocator_state',
        'evolutionary_memory',
        'evolutionary_scores',
        'evolutionary_population',
        'risk_events',
        'risk_state',
        'risk_metrics',
        'bot_state',
        'runtime_state',
        'orders',
        'fills',
        'proposed_fills',
        'execution_audit',
        'entry_audit',
        'exit_audit'
    ];
begin
    foreach t in array reset_tables loop
        if exists (
            select 1
            from information_schema.tables
            where table_schema='public'
              and table_name=t
        ) then
            execute format('delete from %I', t);
            raise notice '[QFOS_FULL_RESET] cleared table=%', t;
        else
            raise notice '[QFOS_FULL_RESET] table_missing_skip=%', t;
        end if;
    end loop;
end $$;
