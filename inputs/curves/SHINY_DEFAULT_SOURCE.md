# GIFT default curve source

The **GIFT Shiny Default** preset uses the three graph-derived CSVs supplied
by the user on 2026-10-26. The data are copied unchanged, with no smoothing,
renormalization, extension, or conversion. Depth is in metres, velocity in m/s,
and substrate class bounds in millimetres. Class lower bounds are inclusive;
upper bounds are exclusive. These are user-authorized graph-derived defaults,
rather than independently recovered source tables from the live Shiny server.

| Curve | Original attachment | App filename | Rows |
| --- | --- | --- | --- |
| Depth | chinook_juvenile_rearing_depth_from_graph(1).csv | gift_shiny_default_depth.csv | 351 |
| Velocity | chinook_juvenile_rearing_velocity_from_graph(1).csv | gift_shiny_default_velocity.csv | 351 |
| Substrate | chinook_juvenile_rearing_substrate_from_graph(1).csv | gift_shiny_default_substrate.csv | 72 |

SHA-256 checksums are retained in
`tests/regression_reference/gift_shiny_defaults.json` and tested against the
packaged CSV bytes. Curve validation also checks numeric ranges, ordered
coordinates, and nonoverlapping substrate classes. The existing life-stage
curves and user-upload controls remain available.
