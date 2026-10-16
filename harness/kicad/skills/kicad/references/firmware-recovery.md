# Authorized flashing with a recoverable previous version

Apply when the user asks the agent to flash a named project/device, including standing permission
to flash completed firmware automatically. Record that permission and its scope in the project's
`firmware/FLASH-POLICY.md`; link it from the firmware README. Continue within that scope without
asking again per build. A later no-flash instruction wins. Without permission, deliver the build
and Flash recipe for the person. This file is instruction, not a background flashing service.

## Before every write

- Build the candidate from the current source and run relevant logic/protocol tests. Check its
  GPIO/voltage/assembly variant against the actual target and wiring; hash the exact binaries
  to write. A documentation-only turn or unchanged build is not a reason to reflash.
- Pin the version currently on the device before replacing it. Preserve the known-good stable
  checkpoint too if it differs. Use a new immutable tag/checkpoint or recovery bundle for each
  attempt; never force-move an earlier tag or overwrite its binary/backup. Keep source/config,
  build/tool versions, full image set and offsets, SHA256 hashes and recovery commands together.
  A source-only tag is insufficient. Archive uncommitted source separately rather than committing
  unrelated work or committing/pushing main to make a tag. Remote publication is a separate scope.
- Verify the actual previous image, not just a possibly stale `stable.json` label. Where supported,
  read the complete detected flash back and verify it against the device before writing; retain
  calibration/settings partitions too. Other targets need a supported, verified recovery artifact.
  If the installed source revision is unknown, say so and preserve its exact restorable readback;
  do not invent a source-to-binary match. Keep device dumps local rather than publishing them.
- Record the device fingerprint and exact target (chip/unique identity and current port/volume).
  A vendor ID alone is not unique. Revalidate identity before writes and rollback, including after
  USB re-enumeration; never guess between devices. Use the existing project's verified flashing
  route/recipe. Missing device, failed backup or ambiguous identity is an unmet prerequisite,
  not a reason to erase/retry blindly or repeat a permission request already answered.
- Record who owns the serial port, pause only that target's conflicting bridge/listener, and plan
  restoration of its prior state even on failure. A bootloader or reset probe can briefly claim
  the port; use bounded retries and report the concrete failure if recovery cannot proceed.

## Write, verify and retain the recovery path

- Write only the identified device using the hashed candidate images. Verify written bytes with
  the platform's supported verification, then check boot and the expected protocol/health reply.
  For moving hardware, keep outputs disabled during this firmware check; authorization to flash
  does not authorize actuator tests. Do not change calibration or wiring assumptions to force a pass.
- Save a per-attempt receipt and logs: previous checkpoint/readback/hash, target fingerprint,
  candidate source/binary hashes, write verification, boot/protocol result, rollback and restored
  serial-service status. `firmware/flash-result.json` may point to the latest attempt; preserve
  earlier receipts and link the exact restore command in the README.
- If verification/boot fails and the same target and backup are available, automatically restore
  the pinned previous image within the flash authorization and verify restoration. If restoration
  fails, retain all artifacts and report the actual state; never claim the device was recovered.
  Restore the bridge/listener to its prior state in either outcome.
- Keep the new image running when its checks pass; do not unconditionally restore the old image
  at the end of a successful update. A successful write does not silently replace the user's
  known-good stable designation. Keep old stable tags and record actual physical observations
  separately from byte/USB verification. Report the installed version and the recovery checkpoint.
