"""Run shell commands on a phone, and answer questions about its real state.
[phase 2]

Responsibility:
- wrap /shell/execute into a plain `run(phone_id, cmd) -> stdout`
- provide the verification primitives every flow depends on:
    * which Google accounts are really present (dumpsys account)
    * whether a package is really installed (pm list packages)
- own text entry, including the escaping needed for passwords with symbols
  (`input text` mangles spaces and shell metacharacters)

This module is the project's only source of truth. GeeLark's RPA tasks report
success without having done anything, so every flow must confirm its result
here rather than trusting a task status.
"""
