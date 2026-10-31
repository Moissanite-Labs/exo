# Configure worker models inside Exo

In the terminal client, type `/workers`. In the desktop or web app, open **Providers → Worker models**. Both interfaces edit the same connected core through a principal-only API.

Choose routes for **fast**, **standard** and **deep**. The first route is preferred. The remaining routes are fallbacks in order. Use the model search or type a `provider:model` reference directly. In the terminal form, separate routes with commas. In the desktop form, put fallback routes on separate lines.

Saving validates all three tiers, writes their defaults to the Exo configuration and applies them to the connected core. Subsequent model calls use the new routes, including calls from existing workers. Requests already in flight continue. The main conversation model and reasoning settings stay unchanged.

The panel shows each tier's effective fallback order. Existing router inheritance still applies: fast can fall back through standard and main, while standard and deep can fall back through main. An empty tier inherits those routes. Removing a model from one tier does not exclude it from other tiers or inherited main routes.

These are global defaults for future starts. Other running cores pick them up on their next start. A profile with its own worker routes, or a configured `delegate` role that overrides tier routing, blocks this editor and explains why. Those overrides need editing at their source.

If another window changes model settings, Exo refuses a stale save. The draft stays visible. **Reload settings** reads the current routes and replaces the form's draft. Failed or unconfirmed requests never report success.

Model references contain provider names and model identifiers. Keys and tokens belong in the existing provider credential controls.

## Adaptive selection

Adaptive selection, approved and excluded model pools, low/medium/high budget preferences and authority for an agent to choose models are proposed in [the feature request](https://github.com/Moissanite-Labs/exo/discussions/1016). These controls do not grant an agent authority to change model settings or spending policy.
