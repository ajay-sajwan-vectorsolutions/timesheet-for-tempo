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

# 3 E003
```
1. I got the notification at around 8AM today that the tempo was synched. However that time is not configured as per my settings. My tray menu says it is set for 11AM.
2. Yesterday when i tried doing a sync from the tray by cliking the icon, it didnt show up the notification. Can you check why. was it because i was on PTO. is that true? Ideally the notification should always display whenever there is an action taken by user or a sync or config updated by the sceduled job.
3. Another point, Check if there is any instance which is causing the auto sync to run at a time different than what is configured in tray.
For eg; 1. After a fresh installation, is it honoring the time configured from the tray
        2. It should always honor whats in the config
        3. Whatever is the default time set at the time of fresh installation, it should honor that unless there is a different time in cofig
```

# 4 E004
```
Earlier we had worked on a task which was realted to security. When i share the zip file to team, it was getting deleted by their system.
We did some fixes for it. Can you check how the current install.bat file code stands against that issue. because at this point when i share the zip file, it is again getting deleted.
```

# 5 E005
```
1. During initial setup, lets comment the code asking for Email notifications. we should add it in future todo list
2. if previous installation is identified, lets use the overhead stories configured previously as well
3. At the end when we display timer countdown 'This window will close in:', can we change this to like other terminal windows where we ask user to press a key to exit
```