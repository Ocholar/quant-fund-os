create or replace function qfos_db_prevent_oversell_v1()
returns trigger as $$
declare
    open_qty double precision;
    sell_qty double precision;
    diff double precision;
    dust_tolerance double precision := 0.0001;
begin
    if lower(coalesce(new.side, '')) <> 'sell' then
        return new;
    end if;

    select coalesce(
        sum(
            case
                when lower(side) = 'buy' then quantity
                when lower(side) = 'sell' then -quantity
                else 0
            end
        ),
        0
    )
    into open_qty
    from trades
    where symbol = new.symbol;

    sell_qty := coalesce(new.quantity, 0);
    diff := sell_qty - open_qty;

    if open_qty <= 0.00000001 then
        raise exception '[QFOS_DB_OVERSELL_GUARD] reject sell_no_open_position symbol=% sell_qty=% open_qty=%',
            new.symbol, sell_qty, open_qty;
    end if;

    if diff > dust_tolerance then
        raise exception '[QFOS_DB_OVERSELL_GUARD] reject sell_qty_exceeds_open symbol=% sell_qty=% open_qty=% diff=% tolerance=%',
            new.symbol, sell_qty, open_qty, diff, dust_tolerance;
    end if;

    if diff > 0 then
        raise notice '[QFOS_DB_OVERSELL_GUARD] clamp sell_qty symbol=% old_qty=% open_qty=% diff=%',
            new.symbol, sell_qty, open_qty, diff;

        new.quantity := open_qty;
    end if;

    return new;
end;
$$ language plpgsql;

drop trigger if exists qfos_db_prevent_oversell_before_insert_v1 on trades;

create trigger qfos_db_prevent_oversell_before_insert_v1
before insert on trades
for each row
execute function qfos_db_prevent_oversell_v1();
