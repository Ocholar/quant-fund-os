do $$
declare
    t text;
    reset_tables text[] := array[
        'trades',
        'positions',
        'portfolio_snapshots',
        'symbol_quarantine',
        'strategy_quarantine',
        'strategy_scores',
        'profit_engine_peaks',
        'profit_engine_state',
        'qfos_exit_lifecycle_state'
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
            raise notice '[QFOS_CLEAN_PERFORMANCE_RESET] cleared table=%', t;
        else
            raise notice '[QFOS_CLEAN_PERFORMANCE_RESET] table_missing_skip=%', t;
        end if;
    end loop;
end $$;
