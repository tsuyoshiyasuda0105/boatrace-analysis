# Failure log examples

## Example 1

- what was attempted: Ran Playwright smoke after editing auth flow
- exact failure: Login test stayed on the login page instead of reaching the dashboard
- root cause: Test role route was disabled because TESTING mode was off
- prevention step: Confirm TESTING mode before Playwright smoke
- rule updated: Add TESTING check to the start-of-test checklist

## Example 2

- what was attempted: Generated a shared skill with UI metadata
- exact failure: Skill initialization rejected the short description length
- root cause: UI field constraints were not checked before generation
- prevention step: Read the openai.yaml field constraints before creating agents metadata
- rule updated: Add metadata validation before initializing new shared skills
