#!/usr/bin/env python3
# coding=UTF-8
#
# Cross Drive Analysis tool for bulk extractor.
# V4
# Features of this program:
# --netmap  -- makes a map of which computers exchanged packets (ethernet maps)
# --makestop  -- Creates a stoplist of features that are on more than a fraction of the drives
# --threshold -- sets the fraction of drives necessary for a feature to be ignored
# --idfeatures  -- spcifies which feature files are used for identity operations
#
# reads multiple bulk_extractor histogram files and outputs:
# stoplist.txt - list of email addresses on more than 1/3 of the disks
# targets.txt  - list of email addresses not on stoplist and the # of drives on which they appear.
#
# Version 1.3 - Complete rewrite; elimiantes driveids and featureids, since strings
#               in Python are hashable (and thus integers). Also uses bulk_extractor_reader

__version__='1.3.1'
import os.path,sys

#if sys.version_info < (3,2):
#    raise RuntimeError("Requires Python 3.2 or above")

import os,sys,re,collections,sqlite3

# add paths in an attempt to find our modules
if os.getenv("DOMEX_HOME"):
    sys.path.append(os.getenv("DOMEX_HOME") + "/src/lib/") # add the library
sys.path.append("../lib/")      # add the library


# Replace this with an ORM?
schema = \
"""
PRAGMA cache_size = 200000;
CREATE TABLE IF NOT EXISTS drives (driveid INTEGER PRIMARY KEY,drivename TEXT NOT NULL UNIQUE,ingested DATE);
CREATE TABLE IF NOT EXISTS features (featureid INTEGER PRIMARY KEY,feature TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXSITS features_idx ON features (feature);
CREATE TABLE IF NOT EXISTS feature_drive_counts (driveid INTEGER NOT NULL,feature_type INTEGER NOT NULL,featureid INTEGER NOT NULL,count INTEGER NOT NULL) ;
CREATE INDEX IF NOT EXISTS feature_drive_counts_idx1 ON feature_drive_counts(featureid);
CREATE INDEX IF NOT EXISTS feature_drive_counts_idx2 ON feature_drive_counts(count);
CREATE TABLE IF NOT EXISTS feature_frequencies (id INTEGER PRIMARY KEY,feature_type INTEGER NOT NULL,featureid INTEGER NOT NULL,drivecount INTEGER,featurecount INTEGER);
CREATE INDEX feature_frequences_idx ON feature_frequencies (featureid);
"""

"""Explaination of tables:
drives         - list of drives that have been ingested.
features       - table of all features
feature_drive_counts - count of all features per drive
feature_frequencies    - for each feature type, a count of the number of drives on which it appears, and the total number of times in the collection
"""


import ttable, bulk_extractor_reader

SEARCH_TYPE = 1
EMAIL_TYPE = 2
WINPE_TYPE = 3

def create_schema():
    # If the schema doesn't exist, create it
    c = conn.cursor()
    for line in schema.split(";"):
        print(line)
        c.execute(line)

def get_driveid(drivename):
    c = conn.cursor()
    c.execute("INSERT INTO drives (driveid,drivename) VALUES (NULL,?)",(drivename,))
    return c.lastrowid

def get_drivename(driveid):
    c = conn.cursor()
    c.execute("SELECT drivename from drives where driveid=?",(driveid,))
    return c.fetchone()[0]

def get_featureid(feature):
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO features (featureid,feature) VALUES (NULL,?)",(feature,))
    c.execute("SELECT featureid from features where feature=?",(feature,))
    return c.fetchone()[0]

def list_drives():
    c = conn.cursor()
    for (rowid,drivename) in c.execute("SELECT rowid,drivename FROM drives"):
        print("{:3} {}".format(rowid,drivename))

def feature_drive_count(featureid):
    assert(type(featureid)==int)
    c = conn.cursor()
    c.execute("SELECT count(*) from feature_drive_counts where featureid=? ",(featureid,))
    return c.fetchone()[0]

def ingest(report):
    import time
    c = conn.cursor()
    br = bulk_extractor_reader.BulkReport(report)
    driveid = get_driveid(br.image_filename())
    print("Ingesting {} as driveid {}".format(br.image_filename(),driveid))

    t0 = time.time()
    
    # Make sure that this driveid is not in the feature tables
    c.execute("DELETE FROM feature_drive_counts where driveid=?",(driveid,))

    # initial version we are ingesting search terms, winpe executables, and email addresses
    for (search,count) in br.read_histogram_entries("url_searches.txt"):
        if search.startswith(b"cache:"): continue  
        featureid = get_featureid(search);
        c.execute("INSERT INTO feature_drive_counts (driveid,feature_type,featureid,count) values (?,?,?,?);",
                  (driveid,SEARCH_TYPE,featureid,count))
        
    # Add counts for email addresses
    for (email,count) in br.read_histogram_entries("email_histogram.txt"):
        #print("Add email {} = {}".format(email,count))
        featureid = get_featureid(email);
        c.execute("INSERT INTO feature_drive_counts (driveid,feature_type,featureid,count) values (?,?,?,?);",
                  (driveid,EMAIL_TYPE,featureid,count))

    # Add hashes for Windows executables
    import collections
    pe_header_counts = collections.Counter()
    for (pos,feature,context) in br.read_features("winpe.txt"):
        featureid = get_featureid(feature)
        pe_header_counts[featureid] += 1
    for (featureid,count) in pe_header_counts.items():
        c.execute("INSERT INTO feature_drive_counts (driveid,feature_type,featureid,count) values (?,?,?,?);",
                  (driveid,WINPE_TYPE,featureid,count))
    conn.commit()
    t1 = time.time()
    print("Driveid {} imported in {} seconds\n".format(driveid,t1-t0))


def correlate_for_type(driveid,feature_type):
    c = conn.cursor()
    res = []
    c.execute("SELECT R.drivecount,C.featureid,feature FROM feature_drive_counts as C JOIN features as F ON C.featureid = F.rowid JOIN feature_frequencies as R ON C.featureid = R.featureid where C.driveid=? and C.feature_type=?",(driveid,feature_type))
    res = c.fetchall()
    # Strangely, when we add an ' order by R.drivecount where R.drivecount>1' above it kills performance
    # So we just do those two operations manually
    res = filter(lambda r:r[0]>1,sorted(res))
    # Now, for each feature, calculate the drive correlation
    coefs = {}                  # the coefficients
    contribs = {}
    for line in res:
        print(line)
        (drivecount,featureid,feature) = line
        for (driveid_,) in c.execute("select driveid from feature_drive_counts where featureid=? and driveid!=?",
                                     (featureid,driveid)):
            print("  also on drive {}".format(driveid_))
            if driveid_ not in coefs: coefs[driveid_] = 0; contribs[driveid_] = []
            coefs[driveid_] += 1.0/drivecount
            contribs[driveid_].append([1.0/drivecount,featureid,feature])
        if drivecount > args.drive_threshold:
            break
    for (driveid_,coef) in sorted(coefs.items(),key=lambda a:a[1],reverse=True):
        print("Drive {} {}".format(driveid_,get_drivename(driveid_)))
        print("Correlation: {:.6}".format(coef))
        for (weight,featureid,feature) in sorted(contribs[driveid_],reverse=True):
            print("   {:.2}   {}".format(weight,feature))
        print("")

def make_report(driveid):
    c.execute("select count(*) from feature_frequences")
    if c.fetchone()[0]==0:
        build_feature_frequences()
    print("Report for drive: {} {}".format(driveid,get_drivename(driveid)))
    print("Email correlation report:")
    correlate_for_type(driveid,EMAIL_TYPE)
    print("Search correlation report:")
    correlate_for_type(driveid,SEARCH_TYPE)
    print("WINPE correlation report:")
    correlate_for_type(driveid,WINPE_TYPE)
        

def test():
    a = get_featureid("A")
    b = get_featureid("B")
    assert(a!=b)
    assert(get_featureid("A")==a)
    assert(get_featureid("B")==b)
    conn.commit()


def build_feature_frequences():
    print("Building feature frequences...")
    c = conn.cursor()
    c.execute("delete from feature_frequencies")
    c.execute("insert into feature_frequencies (featureid,feature_type,drivecount,featurecount) select featureid,feature_type,count(*),sum(count) from feature_drive_counts group by featureid,feature_type")
    conn.commit()
    print("Feature frequences built.")
    

if(__name__=="__main__"):
    import argparse,xml.parsers.expat
    parser = argparse.ArgumentParser(description='Cross Drive Analysis with bulk_extractor output')
    parser.add_argument("--ingest",help="Ingest a new BE report",action="store_true")
    parser.add_argument("--list",help="List the reports in the database",action='store_true')
    parser.add_argument("--recalc",help="Recalculate all of the feature counts in database",action='store_true')
    parser.add_argument("--test",help="Test the script",action="store_true")
    parser.add_argument("--report",help="Generate a report for a specific driveid",type=int)
    parser.add_argument("--build",help="build feature_frequences",action='store_true')
    parser.add_argument('reports', type=str, nargs='*', help='bulk_extractor report directories or ZIP files')
    parser.add_argument("--drive_threshold",type=int,help="don't show features on more than this number of drives",default=10)
    parser.add_argument("--correlation_cutoff",type=float,help="don't show correlation drives for coefficient less than this",default=0.5)
    args = parser.parse_args()

    if args.test:
        try:
            os.unlink('cda2_tool.test.db')
        except FileNotFoundError:
            pass
        conn = sqlite3.connect('cda2_tool.test.db')
        create_schema()
        test()

    conn = sqlite3.connect('cda2_tool.db')

    if args.ingest:
        create_schema()
        for fn in args.reports:
            ingest(fn)

    if args.build:
        build_feature_frequencies()

    if args.list:
        list_drives()

    if args.report:
        make_report(args.report)
