# 1. E001

```
## 1.1 Developers only - Only add hours for assigned user story on that day
1. The system should only add hours against the Jira user sory which is currently assigned and is being worked by the user. The story should be in 'In Development' or 'Code Review' state. This is hard requirement for all users who are 'Developers'
2. If the user story state is not 'In Development' or 'Code Review', then it means for that user the story is no longer being worked upon on that partivular day. The user had already worked in past and completed his set of work. so the system should not log hours against that user story for that user on now.

## 1.2 QA only - Only add hours for assigned user story on that day 
1. The system should only add hours against the Jira user sory which is currently assigned and is being worked by the user. The story should be in 'Testing' or 'User Acceptance Testing' state. This is hard requirement for all users who are 'QA'
2. Same requirememt for QA as mentioned for developers in 1.1 -> 2
```

# 2. E002
```
## 2.1 How to test Monthly Submission without submiting to Tempo
1. How to test without subitting to tempo
2. Can you mock the monthly submission logic so that we can verify it is working correctly without impacting the actual Tempo submission for that user.
```