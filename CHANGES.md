This is a change-history and list of contributors to the script.

## 2012.10.14 by [Joel Thornton](http://github.com/joelpt) ##

  - Improved compatibility with respect to comments migrated by a previous version of
    this script.


## 2012.09.28 by [David Schnur](http://github.com/dnschur) ##

https://github.com/dnschnur/google-code-issues-migrator

### New Features ###

 - Greatly optimized Github API usage, allowing the script to process several times as
   many issues before reaching the API's hourly rate-limit.

 - The script now tries to avoid hitting the Github API's rate-limit part-way through
   processing an issue, to avoid leaving it in an incomplete state.

 - Improved support of duplicate / merged issues, by detecting the 'merged into' update
   and generating a Github comment pointing to the parent issue.  The automatically-added
   'issue x is a duplicate of this issue' comments are now filtered out, since Github
   already shows a reference when the duplicate links back to the parent.

 - Added migration of Google Code statuses like 'invalid', 'wontfix' and 'duplicate';
   these now map to the matching Github tags.

 - The script now accepts Github projects in the form user/project, where user can be an
   organization or a different user from the one running the script.  This still requires
   that the user running the script have enough permissions on the repository, of course.

 - Added an option to keep issue numbers in sync, by handling cases where Google Code
   skipped an issue number.

 - New issues are now marked closed after all comments have been added, to better mimic
   the order of that update in most real-world cases.

 - Added an option to automatically assign issues that have an owner in Google Code.

 - Added an option to omit migration of Google Code Priority labels.

### Bug Fixes ###

 - Comments containing percent-signs are no longer added repeatedly when the script is run
   multiple times.


## 2012.09.24 by [Jake Biesinger](http://github.com/jakebiesinger) ##

https://github.com/jakebiesinger/google-code-issues-migrator

 - Switched to PyGithub in order to support the Github v3 API.


## Original version by [Arthur Debert](http://github.com/arthur-debert) (and many other contributors) ##

http://github.com/arthur-debert/google-code-issues-migrator