import csv
import logging
import datetime
import re
from StringIO import StringIO

import httplib2
from github2.client import Github
from BeautifulSoup import BeautifulSoup

options = None

logging.basicConfig(level=logging.DEBUG)

g_statusre = \
  '^('                                                         + \
  'Issue has not had initial review yet'                 + '|' + \
  'Problem reproduced \/ Need acknowledged'              + '|' + \
  'Work on this issue has begun'                         + '|' + \
  'Waiting on feedback or additional information'        + '|' + \
  'Developer made source code changes, QA should verify' + '|' + \
  'QA has verified that the fix worked'                  + '|' + \
  'This was not a valid issue report'                    + '|' + \
  'Unable to reproduce the issue'                        + '|' + \
  'This report duplicates an existing issue'             + '|' + \
  'We decided to not take action on this issue'          + '|' + \
  'The requested non-coding task was completed'                + \
  ')$'

def get_url_content(url):
    h = httplib2.Http(".cache")
    resp, content = h.request(url, "GET")
    return content

class IssueComment(object):
    def __init__(self, date, author, body):
        self.created_at  = date
        self.body_raw = body
        self.author = author
        self.user = options.github_user_name

    @property    
    def body (self):
        return ("%s - %s \n%s" % (self.author, self.created_at, self.body_raw)).encode('utf-8')

    def __repr__(self):
        return self.body.encode('utf-8')
        
class Issue(object):

    def __init__(self, issue_line):
        for k,v in issue_line.items():
            setattr(self, k.lower(), v)
        logging.info("Issue #%s: %s" % (self.id, self.summary))
        self.get_original_data() 

    def parse_date(self, node):
        datenode = node.find(attrs={'class' : 'date'})
        datestring = datenode['title']
        try:
            return datetime.datetime.strptime(datestring, '%a %b %d %H:%M:%S %Y')
        except ValueError:     # if can't parse time, just assume now
            return datetime.datetime.now

    def get_user(self, node):
        authornode = node.find(attrs={'class' : 'author'})
        userhrefnode = authornode.find(attrs={'href' : re.compile('^\/u\/')})
        return userhrefnode.string

    def get_body(self,node):
        comment = unicode(node.find('pre').renderContents(), 'utf-8', 'replace')
        return comment

    def get_labels(self, soup):
        self.labels = []
        self.milestones = [] # Milestones are a form of label in googlecode
        for node in soup.findAll(attrs = { 'class' : 'label' }):
            label = unicode(re.sub('<\/?b>', '', node.renderContents()))
            if re.match('^Milestone-', label):
                self.milestones.append(re.sub('^Milestone-', '', label))
            else:
                self.labels.append(label)
        return

    def get_status(self, soup):
        node = soup.find(name = 'span', attrs = { 'title' : re.compile(g_statusre) })
        self.status = unicode(node.string)
        self.labels.append("Status-%s" % self.status)
        return
	
            
    def get_original_data(self):
        logging.info("GET %s" % self.original_url)
        content = get_url_content(self.original_url)
        soup = BeautifulSoup(content)
        descriptionnode = soup.find(attrs={'class' : "cursor_off vt issuedescription"})
        descriptionstring = unicode(descriptionnode.find('pre').renderContents(), 'utf-8', 'replace')
        self.body = unicode("%s<br />Original link: %s" % (descriptionstring , self.original_url))
        datenode = descriptionnode.find(attrs={'class' : 'date'})
        datestring = datenode['title']
        try:
            self.created_at = datetime.datetime.strptime(datestring, '%a %b %d %H:%M:%S %Y')
        except ValueError:     # if can't parse time, just assume now
            self.created_at = datetime.datetime.now
        comments = []
        for node in soup.findAll(attrs={'class' : "cursor_off vt issuecomment"}):
            try:
                date = self.parse_date(node)
                author  = self.get_user(node)
                body = self.get_body(node)
                if not re.match('^\\n<i>\(No comment was entered for this change\.\)<\/i>\\n$', body):
                    comments.append(IssueComment(date, author, body))
            except:
                pass
        self.comments = comments    
        logging.info('got comments %s' %  len(comments))
        self.get_labels(soup)
        logging.info('got labels %s' % len(self.labels))
        logging.info('got milestones %s' % len(self.milestones))
        self.get_status(soup)

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
    github = Github(username=options.github_user_name, api_token=options.github_api_token, requests_per_second=1)
    if issue.status.lower()  in "invalid closed fixed wontfix verified worksforme duplicate done".lower():
        issue.status = 'closed'
    else:
        issue.status = 'open'
    try:    
        git_issue = github.issues.show(options.github_project, int(issue.id))
        logging.warn( "skipping issue : %s" % (issue))
    except RuntimeError:
        title = "%s" % issue.summary
        logging.info('will post issue:%s' % issue)        
        logging.info("issue did not exist")
        git_issue = github.issues.open(options.github_project, 
            title = title,
            body = issue.body
        )
    if issue.status == 'closed':
        github.issues.close(options.github_project, git_issue.number)
    if sync_comments is False:
        return git_issue
    old_comments  = github.issues.comments(options.github_project, git_issue.number)
    for i,comment in enumerate(issue.comments):

        exists = False
        for old_c in old_comments:  
            # issue status changes have empty bodies in google code , exclude those:
            if bool(old_c.body) or old_c.body == comment.body :
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
    global options
    options, args = parser.parse_args(args=sys.argv, values=None)
    try:               
        issues_data = download_issues()
        process_issues(issues_data)
    except:
        parser.print_help()    
        raise