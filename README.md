This is a simple script to migrate issues from Google Code to Github.

For a full history of changes, please
consult the [change log](https://github.com/arthur-debert/google-code-issues-migrator/blob/master/CHANGES.md).

### How it works ###

The script iterates over the issues and comments in a Google Code repository, creating
matching issues and comments in Github.  This has some limitations:

 - All migrated issues and comments are authored by the user running the script, and lose
   their original creation date.  We try to mitigate this by adding a non-obtrusive header
   to each issue and comment stating the original author and creation date.

 - Attachments are lost, since Github doesn't support them in issues or comments.

Otherwise almost everything is preserved, including labels, issue state (open/closed),
issue status (invalid, wontfix, duplicate) and merged-into links for duplicate issues.

The script can be run repeatedly to migrate new issues and comments, without mucking up
what's already on Github.

### Required Python libraries ###

* [gdata](http://code.google.com/p/gdata-python-client/) -- `pip install gdata`
* [PyGithub](https://github.com/jacquev6/PyGithub/) -- `pip install PyGithub` -- v1.8.0+ required

### Usage ###

	migrateissues.py [options] <google project name> <github username> <github project>

	  google_project_name 	    The project name (from the URL) from google code
	  github_user_name 	        The Github username
	  github_project 	        The Github project name, e.g. username/project

	Options:
	  -h, --help                show this help message and exit
	  -a, --assign-owner        Assign owned issues to the Github user
	  -d, --dry-run             Don't modify anything on Github
	  -p, --omit-priority       Don't migrate priority labels
	  -s, --synchronize-ids     Ensure that migrated issues keep the same ID
	  --skip-closed             Skip all closed bugs

        You will be prompted for your github password.

`--assign-owner` automatically assigns any issues that currently have an owner to your
Github user (the one running the script), even if you weren't the original owner.  This
is used to save a little time in cases where you do in fact own most issues.

`--dry-run` does as much as possible without actually adding anything to Github.  It's
useful as a test, to turn up any errors or unexpected behaviors before you run the script,
irreversibly, on your real repository.

`--omit-priorities` skips migration of Google Code Priority labels, since many projects
don't actually use them, and would just remove them from Github anyway.

`--synchronize-ids` attempts to ensure that every Github issue gets the same ID as its
original Google Code issue.  Normally this happens anyway, but in some cases Google Code
skips issue numbers; this option fills the gaps with dummy issues to ensure that the next
real issue keeps the same numbering.  This only works, of course, if the migration starts
with a fresh Github repistory.

`--skip-closed` will skip migrating issues that were closed.
