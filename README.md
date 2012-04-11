This is a simple script to move issues from google code to github.

Some liberties have been taken (as we cannot, for example, know which google user corresponds to other user on github). But most information is complete.

This script can be run repeatedly and will just pull in new issues and new comments without mucking up what's already on github.

Required Python libraries:

* [gdata](http://code.google.com/p/gdata-python-client/) -- `pip install gdata`
* [python github](http://github.com/ask/python-github2) -- `pip install github2`

Usage:

	migrate-issues.py [options] <google_project_name> <github_api_token> <github_user_name> <github_project>

	  google_project_name 	The project name (from the URL) from google code
	  github_api_token 	    Your Github api token
	  github_user_name 	    The Github username
	  github_project 	    The Github project name, e.g. username/project

	Options:
	  -h, --help            show this help message and exit
	  -d, --dry-run			don't modify anything on Github
