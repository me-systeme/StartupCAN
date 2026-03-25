# 25.03.2026

## CAN Endpoint Consistency and CV_ID Handling

### Context

GSV CAN devices expose multiple CAN-related identifiers:

* `CMD_ID` (command ID)
* `ANSWER_ID` (response ID)
* `CV_ID` (cyclic value frame ID)

In practice, these IDs can be configured independently on the device.

However, allowing independent configuration introduces additional complexity:

* ambiguity during device verification
* increased YAML configuration complexity
* risk of inconsistent or partially configured devices
* unclear definition of a “valid” endpoint state

Additionally, the workflow includes a **“same endpoint skip” optimization**, which must be safe and deterministic.

---

### Decision

StartupCAN enforces the following invariant:

> **`CV_ID` is always equal to `ANSWER_ID`**

This leads to the following design decisions:

1. **No CV_ID in YAML**

   * `CV_ID` is not exposed in `current.ids` or `new.ids`
   * it is treated as a derived/internal value

2. **CV_ID is always written explicitly**

   * during reconfiguration:

     ```python
     CV_ID = ANSWER_ID
     ```

3. **CV_ID is always verified**

   * `_verify_ids()` checks:

     * `CMD_ID == expected`
     * `ANSWER_ID == expected`
     * `CV_ID == ANSWER_ID`
     * `CANBAUD == expected`

4. **State probe requires full consistency**

   * a probe is only considered successful if:

     * activation works **and**
     * all values including `CV_ID` match expectations

5. **Same-endpoint skip requires full verification**

   * skipping reconfiguration is allowed only if:

     * planned old == planned new (CMD, ANS, BAUD)
     * AND device readback confirms:

       * `CMD_ID`
       * `ANSWER_ID`
       * `CV_ID == ANSWER_ID`
       * `CANBAUD`

---

### Rationale

This approach intentionally trades flexibility for robustness and simplicity.

**Benefits:**

* eliminates an entire class of misconfiguration bugs
* simplifies YAML structure and user mental model
* ensures consistent device state after every run
* makes verification deterministic and strict
* avoids false positives in “same endpoint” detection

**Key insight:**

> Activation success alone is not sufficient to trust device state.

A device may still:

* have a wrong `CV_ID`
* have mismatched internal configuration
* behave inconsistently on the bus

Therefore, **readback verification is treated as the source of truth**.

---

### Consequences

**Positive:**

* safer and more predictable workflow
* consistent CAN behavior across all devices
* simpler configuration interface
* robust skip logic

**Negative / Trade-offs:**

* loss of flexibility for advanced use cases where `CV_ID != ANSWER_ID`
* additional write operation during configuration
* stricter verification may trigger reconfiguration even if communication “works”

---

### Alternatives considered

1. **Expose CV_ID in YAML**

   * rejected due to:

     * increased complexity
     * low practical need
     * higher risk of invalid configurations

2. **Ignore CV_ID in verification**

   * rejected because:

     * allows silent inconsistencies
     * breaks deterministic state detection

3. **Use activation success as verification**

   * rejected because:

     * insufficient guarantee of correct configuration
     * can hide partial misconfiguration

---

### Summary

StartupCAN defines a CAN endpoint as:

```
(CMD_ID, ANSWER_ID, CANBAUD) with CV_ID = ANSWER_ID
```

A device is only considered **correctly configured** if all of the above are verified via readback.

This ensures that:

* configuration is **complete**
* state detection is **reliable**
* workflow decisions (especially skipping) are **safe**


