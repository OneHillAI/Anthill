**Opening another profile now works in the desktop app.** Creating a second profile worked, but
clicking Open on it (or choosing it in the sidebar switcher) failed with "Command open_profile not
allowed by ACL", so you could never switch. The desktop shell was refusing the request because it was
never granted permission to run it; it is now allowed, only from Anthill's own local window.
