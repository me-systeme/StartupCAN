# CAN Endpoint Consistency and VALUE_ID Handling

## Context

GSV CAN devices expose multiple CAN-related identifiers:

* `CMD_ID` (command ID)
* `ANSWER_ID` (response ID)
* `VALUE_ID` (cyclic value frame ID / CV_ID)

In practice, these IDs can be configured independently on the device.

StartupCAN originally treated `VALUE_ID` as an internal invariant and always
enforced:

> `VALUE_ID = ANSWER_ID`

That model was simple and robust, but it turned out to be too restrictive for
real device configurations where `VALUE_ID` may legitimately differ from
`ANSWER_ID`.

At the same time, the workflow still needs to remain deterministic and safe,
especially for:

* readback verification
* state probing
* YAML round-tripping
* “same endpoint skip” decisions

---

## Decision

StartupCAN now treats `VALUE_ID` as a real endpoint property.

This leads to the following design decisions:

1. **`value_id` is configurable in YAML**

   * `new.ids[*].value_id` is required when `new.default=false`
   * `current.ids[*].value_id` is optional

2. **Target `VALUE_ID` is always written explicitly**

   * during reconfiguration, StartupCAN writes:
     * `CMD_ID`
     * `ANSWER_ID`
     * `VALUE_ID`
     * `CANBAUD`

3. **Readback verification uses `VALUE_ID` only if it is known**

   * `_verify_ids()` checks:
     * `CMD_ID == expected`
     * `ANSWER_ID == expected`
     * `CANBAUD == expected`
     * `VALUE_ID == expected` only if an expected `value_id` is available

4. **State probe is strict for the tested target state**
   * probing `new` always includes `value_id`, because target `value_id` is known
   * probing `old` includes `value_id` only if it is known from `current.ids`

5. **Same-endpoint skip requires full endpoint knowledge**
   * skipping reconfiguration is allowed only if:
     * planned old == planned new (`CMD_ID`, `ANSWER_ID`, `VALUE_ID`, `CANBAUD`)
     * and the device readback fully confirms that state
   * if `value_old` is unknown, skipping is not allowed

6. **Unknown current `VALUE_ID` must remain unknown**
   * if `current.ids[*].value_id` is missing, StartupCAN does not invent one
   * it is not validated
   * it is not used for skip decisions
   * it is only written to `config.updated.yaml` if it is known

---

## Rationale

This approach keeps the workflow safe while allowing real `VALUE_ID`
configurations.

**Benefits:**

* supports devices where `VALUE_ID != ANSWER_ID`
* keeps target-state programming explicit and deterministic
* preserves backward compatibility for simple configurations
* avoids fake assumptions about unknown current `VALUE_ID`s
* keeps skip logic safe

**Key insight:**

> Unknown current `VALUE_ID` is different from known current `VALUE_ID`.

If `value_id` is missing in `current.ids`, StartupCAN can still:
* activate the device
* verify `CMD_ID`
* verify `ANSWER_ID`
* verify `CANBAUD`
* reconfigure the device safely

But it must not:
* claim the full old endpoint is known
* skip reconfiguration based on incomplete endpoint information
* write a guessed `value_id` back into YAML

---

## Consequences

**Positive:**

* more flexible than the old `VALUE_ID = ANSWER_ID` rule
* correct support for devices with independent `VALUE_ID`
* safe handling of incomplete `current.ids`
* deterministic target programming

**Negative / Trade-offs:**

* YAML becomes slightly more complex
* some logic now depends on whether `value_old` is known or unknown
* skip optimization becomes more conservative
* readback of the old state may be only partially verifiable

---

## Alternatives considered

1. **Keep enforcing `VALUE_ID = ANSWER_ID`**
   * rejected because valid target configurations may require a different `VALUE_ID`

2. **Make `value_id` optional everywhere**
   * rejected because target programming must be explicit and deterministic

3. **Guess missing current `value_id` from `answer_id`**
   * rejected because this would silently invent configuration data and could
     produce unsafe skip decisions or misleading YAML output

4. **Ignore `VALUE_ID` in verification**
   * rejected because target-state verification must remain strict

---

## Summary

StartupCAN now defines a full CAN endpoint as:

```text
(CMD_ID, ANSWER_ID, VALUE_ID, CANBAUD)
```

For the **target state**, all four values are always known and verified.

For the **current state**, `VALUE_ID` may be unknown. In that case StartupCAN:

* does not validate it
* does not use it for skip decisions
* does not write it unless it is known

This ensures that:

* target configuration is **explicit**
* old-state handling is **honest**
* state detection is **safe**
* workflow decisions are **deterministic**