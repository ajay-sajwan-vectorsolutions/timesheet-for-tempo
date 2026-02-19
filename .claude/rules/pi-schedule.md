# PI (Program Increment) Schedule Rules

## PI Pattern in Jira

Sprint field values contain PI identifiers in this format:
```
PI.{YY}.{N}.{MON}.{DD}
```
- `YY` = 2-digit year (26 = 2026)
- `N` = PI number within the year (1, 2, 3...)
- `MON` = 3-letter month abbreviation (JAN, FEB, MAR, APR, etc.)
- `DD` = day of month

The pattern may have additional text before or after it in the sprint name.
Use regex to extract: `PI\.(\d{2})\.(\d+)\.([A-Z]{3})\.(\d{1,2})`

## Date Derivation

- **PI end date** = date parsed directly from the pattern (e.g., PI.26.1.JAN.30 = January 30, 2026)
- **Planning week** = next 5 working days immediately after PI end date
- **Next PI start date** = first working day after the planning week

## Example Timeline

```
PI 26.1 ends:       Thu Jan 30, 2026
Planning week:       Feb 2-6 (Mon-Fri, 5 working days)
PI 26.2 starts:      Mon Feb 9, 2026
PI 26.2 ends:        Fri Apr 17, 2026
Planning week:       Apr 20-24
PI 26.3 starts:      Mon Apr 27, 2026
```

## Data Source

- Jira Agile API: `GET /rest/agile/1.0/board/{boardId}/sprint`
- Parse sprint names to extract all PI patterns
- Store computed PI calendar in config.json to avoid repeated API calls
- Refresh only when the current stored PI has expired

## Integration Notes

- Vector Solutions (lmsportal.atlassian.net) uses this convention across all teams
- Every story's Sprint field contains the PI identifier
- Planning week is a gap between PIs (not a sprint, not regular work)
- Use ScheduleManager.is_working_day() for skipping weekends/holidays when counting working days
