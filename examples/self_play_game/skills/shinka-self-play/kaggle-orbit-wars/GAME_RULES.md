# Orbit War — Full Game Rules

Conquer planets rotating around a sun in continuous 2D space. A real-time
strategy game for 2 or 4 players.

## Board

- 100×100 continuous space, origin at top-left.
- Sun centered at (50, 50) with radius 10. Fleets crossing the sun are
  destroyed.
- 4-fold mirror symmetry for planet/comet placement.

## Planets

Each planet: `[id, owner, x, y, radius, ships, production]`.

- owner: player ID (0–3) or -1 (neutral).
- radius: `1 + ln(production)`.
- production: 1–5 ships/turn when owned.
- Orbiting planets (orbital_radius + planet_radius < 50) rotate around the
  sun at a constant angular velocity (0.025–0.05 rad/turn).
- Static planets are further from center and do not rotate.
- 20–40 planets total (5–10 symmetric groups of 4).

## Fleets

Each fleet: `[id, owner, x, y, angle, from_planet_id, ships]`.

Speed: `1.0 + (maxSpeed - 1.0) * (log(ships) / log(1000))^1.5`
(1 ship → speed 1.0; ~1000 ships → speed 6.0).

Fleets travel in straight lines. Removed if: out of bounds, crosses sun,
or collides with a planet (triggers combat).

Launch: `[from_planet_id, direction_angle, num_ships]`. Fleet spawns just
outside the planet's radius.

## Comets

Temporary objects on elliptical paths. Spawn in groups of 4 at steps
50, 150, 250, 350, 450. Radius 1.0, production 1. Speed 4.0 units/turn.
Removed when leaving the board (along with garrisoned ships).

## Turn Order

1. Comet expiration
2. Comet spawning
3. Fleet launch (process player actions)
4. Production (owned planets generate ships)
5. Fleet movement + collision detection
6. Planet rotation + comet movement (swept fleets → combat)
7. Combat resolution

## Combat

When fleets collide with a planet:
1. Group arriving fleets by owner, sum ships.
2. Largest attacker fights second-largest; difference survives.
3. Survivor fights garrison if different owner. Planet changes hands if
   attackers exceed garrison.
4. Tied attackers: all destroyed.

## Scoring

Game ends at step 500 or when one player remains.
Final score = ships on owned planets + ships in owned fleets.

## Observation Fields

| Field | Type | Description |
|-------|------|-------------|
| `planets` | list | All planets including comets |
| `fleets` | list | All active fleets |
| `player` | int | Your player ID (0–3) |
| `angular_velocity` | float | Planet rotation speed (rad/turn) |
| `initial_planets` | list | Planet positions at game start |
| `comets` | list | Active comet group data with paths |
| `comet_planet_ids` | list | Planet IDs that are comets |
| `remainingOverageTime` | float | Remaining overage time budget |

## Action Format

```python
[[from_planet_id, direction_angle, num_ships], ...]
```

Return `[]` for no action.

## Configuration Defaults

| Parameter | Default |
|-----------|---------|
| `episodeSteps` | 500 |
| `actTimeout` | 1s |
| `shipSpeed` | 6.0 |
| `sunRadius` | 10.0 |
| `boardSize` | 100.0 |
| `cometSpeed` | 4.0 |
