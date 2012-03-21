import csv
import logging
import datetime
from StringIO import StringIO

import httplib2
from github2.client import Github
from bs4 import BeautifulSoup

options = None

logging.basicConfig(level=logging.DEBUG)


def get_url_content(url):
    h = httplib2.Http(".cache")
    resp, content = h.request(url, "GET")
    return content


class IssueComment(object):
    def __init__(self, date, author, body):
        self.created_at = date
        self.body_raw = body
        self.author = author
        self.user = options.github_user_name

    @property
    def body(self):
        return ("_%s - %s_\n%s" % (self.author, self.created_at, self.body_raw)).encode('utf-8')

    def __repr__(self):
        return self.body.encode('utf-8')


class Issue(object):

    def __init__(self, issue_line):
        for k, v in issue_line.items():
            setattr(self, k.lower(), v)
        logging.info("Issue #%s: %s" % (self.id, self.summary))
        self.get_original_data()

    def parse_date(self, date_string):
        try:
            return datetime.datetime.strptime(date_string, '%b %d, %Y')
        except ValueError:     # if can't parse time, just assume now
            return datetime.datetime.now

    def get_user(self, node):
        return node.find_all('a')[1].string

    def get_body(self, node):
        return node.find('pre').text

    def get_original_data(self):
        logging.info("GET %s" % self.original_url)
        content = get_url_content(self.original_url)
        soup = BeautifulSoup(content)
        self.body = "%s\n\nOriginal link: %s" % (soup.find('td', 'vt issuedescription').find('pre').text, self.original_url)
        self.created_at = self.parse_date(soup.find('td', 'vt issuedescription').find('span', 'date').string)
        comments = []
        for node in soup.find_all('div', "issuecomment"):
            try:
                date = self.parse_date(node.find('span', 'date').string)
                author = self.get_user(node)
                body = self.get_body(node)

                if body != "(No comment was entered for this change.)":
                    # only add comments that are actual comments.
                    comments.append(IssueComment(date, author, body))
            except:
                pass
        self.comments = comments
        logging.info('got comments %s' % len(comments))

    @property
    def original_url(self):
        gcode_base_url = "http://code.google.com/p/%s/" % options.google_project_name
        return "%sissues/detail?id=%s" % (gcode_base_url, self.id)

    def __repr__(self):
        return u"%s - %s " % (self.id, self.summary)


def download_issues():
    url = "http://code.google.com/p/" + options.google_project_name + "/issues/csv?can=1&q=&colspec=ID%20Type%20Status%20Priority%20Milestone%20Owner%20Summary"
    logging.info('Downloading %s' % url)
    content = get_url_content(url)
    f = StringIO(content)
    return f


def post_to_github(issue, sync_comments=True):
    logging.info('should post %s', issue)
    github = Github(username=options.github_user_name, api_token=options.github_api_token, requests_per_second=0.50)
    if issue.status.lower()  in "invalid closed fixed wontfix verified".lower():
        issue.status = 'closed'
    else:
        issue.status = 'open'
    try:
        git_issue = github.issues.show(options.github_project, int(issue.id))
        logging.warn("skipping issue : %s" % (issue))
    except RuntimeError:
        title = issue.summary
        logging.info('will post issue:%s' % issue)
        logging.info("issue did not exist")
        git_issue = github.issues.open(options.github_project,
            title=title,
            body=issue.body,
            created_at=created_at
        )
    if issue.status == 'closed':
        github.issues.close(options.github_project, git_issue.number)
    if sync_comments is False:
        return git_issue
    old_comments = github.issues.comments(options.github_project, git_issue.number)
    for i, comment in enumerate(issue.comments):
        exists = False
        for old_c in old_comments:
            # issue status changes have empty bodies in google code , exclude those:
            if bool(old_c.body) or old_c.body == comment.body:
                exists = True
                logging.info("Found comment there, skipping")
                break
        if not exists:
            #logging.info('posting comment %s', comment.body.encode('utf-8'))
            try:
                github.issues.comment(options.github_project, git_issue.number, comment)
            except:
                logging.exception("Failed to post comment %s for issue %s" % (i, issue))

    return git_issue


def process_issues(issues_csv, sync_comments=True):
    reader = csv.DictReader(issues_csv)
    issues = [Issue(issue_line) for issue_line in reader]
    [post_to_github(i, sync_comments) for i in issues]


if __name__ == "__main__":
    import optparse
    import sys
    usage = "usage: %prog [options]"
    parser = optparse.OptionParser(usage)
    parser.add_option('-g', '--google-project-name', action="store", dest="google_project_name", help="The project name (from the URL) from google code.")
    parser.add_option('-t', '--github-api-token', action="store", dest="github_api_token", help="Yout Github api token")
    parser.add_option('-u', '--github-user-name', action="store", dest="github_user_name", help="The Github username")
    parser.add_option('-p', '--github-project', action="store", dest="github_project", help="The Github project name:: user-name/project-name")
    options, args = parser.parse_args(args=sys.argv, values=None)
    try:
        issues_data = download_issues()
        process_issues(issues_data)
    except:
        parser.print_help()
        raise
