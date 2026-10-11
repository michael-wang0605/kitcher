# Final architecture reference

Status: finalized for implementation on October 7, 2026.

The complete design is maintained in [../architecture.md](../architecture.md). That document owns the product scope, stack, module boundaries, records, quantity rules, atomic confirmation contract, import lifecycle, defaults, and release constraints.

This entry point replaces the separate proposed target design so there is one authoritative architecture.

## How to execute it

1. Follow [03-execution-plan.md](03-execution-plan.md) from M0 through M5.
2. Use [04-acceptance-plan.md](04-acceptance-plan.md) for milestone completion evidence.
3. Implement [05-operations.md](05-operations.md) for startup, phone access, recovery, and configuration.
4. Update the [milestone status table](README.md) only after the relevant evidence exists.

## Change policy

An implementation finding may require a design change. Record the finding, chosen change, and evidence in the canonical architecture and update dependent documents together. Installing supported patch versions and filling in deployment-specific values follow the settled configuration policy.

Model speed/quality, actual phone certificate trust, and backup restoration are execution gates to measure. They are not architecture decisions left awaiting confirmation.