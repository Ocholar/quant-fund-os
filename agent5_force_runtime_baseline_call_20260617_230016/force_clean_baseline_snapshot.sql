BEGIN;

INSERT INTO portfolio_snapshots (
    equity,
    cash,
    exposure,
    drawdown,
    regime
)
VALUES (
    100.0,
    100.0,
    0.0,
    0.0,
    'SIDEWAYS'
);

COMMIT;
