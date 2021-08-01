from datetime import datetime
import re
import sqlite3
import locale
import os.path
from collections import namedtuple

from anki.decks import DeckManager
from anki.notes import Note
from aqt import mw
from aqt.utils import getFile, showInfo, showText
from aqt.qt import QAction
from anki.utils import ids2str
from .splitter import deconjugate, Splitter, Words


locale.setlocale(locale.LC_ALL, 'ja_JP')

CONFIG = mw.addonManager.getConfig(__name__)

BLACKLIST = ['‐', '・', '△', '×']

Clipping = namedtuple('Clipping', ('kind', 'document', 'page', 'location', 'added', 'content'))
Vocab = namedtuple('Vocab', ('stem', 'word', 'usage', 'timestamp', 'title', 'authors'))

VALID_WORDS = None
# LOOKUP_TO_HIGHLIGHT_THRESHOLD = CONFIG['mins_since_lookup'] * 60 * 1000 # 2 mins in unix timestamp

MECABHITS = 0

#DEBUG vars
# Doesn't update timestamp. Turns off loading indicators to make it easier to showInfo
DEBUG = False
currentTime = datetime.now().strftime("%Y-%m-%d_%H%M")
logName = "kindleAnki" + "_%s.log" % currentTime
logPath = os.path.normpath(os.path.join(mw.col.media.dir(), "..", logName)) 
DEBUG_VOCAB = "虎視眈々"
DETAILED_LOGS = False

def log(logLine):
    with open(logPath, "a+", encoding="utf-8") as logFile:
        logFile.write(f'{logLine}\n')

def getVocabTimestamp(timestamp):
    return datetime.fromtimestamp(timestamp/1000).strftime('%Y-%m-%d %H:%M:%S')

def vocabDebug(state, vocabs, clipping=None, vocab=None, distances=None):
    if not DEBUG:
        return
    
    # clipping_in_vocabs = False
    debug_in_vocabs = len([v for v in vocabs if DEBUG_VOCAB in v.usage])
    if clipping:
        added = parse_clipping_added(clipping.added)
    lastVocabTimestamp = getVocabTimestamp(vocabs[0].timestamp)
    if state == 'before':
        log(f'bfore slice: {len(vocabs)}, debug_in_vocabs: {debug_in_vocabs}, ClippingTimestamp:{added}, last timestamp: {lastVocabTimestamp}')
    elif state == 'after':
        vocabTimestamp = None
        if (vocab):
            vocabTimestamp = getVocabTimestamp(vocab.timestamp)
        log(f'after slice: {len(vocabs)}, debug_in_vocabs: {debug_in_vocabs}, ClippingTimestamp:{added}, last timestamp: {lastVocabTimestamp}, VocabTimestamp: {vocabTimestamp}, clipping content: {clipping.content}, title: {clipping.document}, vocab usage: {vocab.usage}')
        if DETAILED_LOGS:
            for i, v in enumerate(vocabs):
                log([f"{i} Vocab Usage: {v.usage} Timestamp: {getVocabTimestamp(v.timestamp)}\n"])
    elif state == 'notFound':
        log(f'ntFnd slice: {len(vocabs)}, debug_in_vocabs: {debug_in_vocabs}, ClippingTimestamp:{added}, last timestamp: {lastVocabTimestamp}')
    elif state == "distance":
        vocabTimestamp = getVocabTimestamp(vocab.timestamp)
        log(f'dstnc slice: {len(vocabs)}, debug_in_vocabs: {debug_in_vocabs}, ClippingTimestamp:{added}, last timestamp: {lastVocabTimestamp}, VocabTimestamp: {vocabTimestamp}, clipping content: {clipping.content}, title: {clipping.document}, vocab usage: {vocab.usage}, distance: {distances}')
    else:
        log(f'---OG slice: {len(vocabs)}, debug_in_vocabs: {debug_in_vocabs}')


def getDeck(vocab):
    return mw.col.decks.id(CONFIG['deck_name'] + '::' + vocab.title)


def getClippings(path):
    
    with open(path, encoding='utf-8') as file:
        lower_path = path.lower()
        if lower_path.endswith('txt'):
            clippings, bad_clippings = parse_text_clippings(file)
        elif lower_path.endswith('html'):
            clippings, bad_clippings = parse_html_clippings(file)
        else:
            raise RuntimeError(f'Unknown extension in path: {path!r}')


    highlight_clippings = list(highlights_only(clippings))
    clippings_to_add = after_last_added(highlight_clippings, last_added_datetime())
    return highlight_clippings, clippings_to_add, bad_clippings, clippings


def displayResults(highlight_clippings, clippings_to_add, bad_clippings, clippings, addedNotes):
    def info():
        if clippings_to_add:
            yield f'{len(addedNotes)} new highlights imported'

        num_old_highlights = len(highlight_clippings) - len(clippings_to_add)
        if num_old_highlights:
            yield f'{num_old_highlights} old highlights ignored'

        num_not_highlights = len(clippings) - len(highlight_clippings)
        if num_not_highlights:
            yield f'{num_not_highlights} non-highlight clippings ignored'

    
    if bad_clippings:
        showText(f'The following {len(bad_clippings)} clippings could not be parsed:\n\n' + '\n==========\n'.join(bad_clippings))

    info_strings = list(info())
    if info_strings:
        showInfo(', '.join(info_strings) + '.')
    else:
        showInfo('No clippings found.')


def setLastAdded(last_added):
    if last_added:
        if not DEBUG:
            CONFIG['last_added'] = parse_clipping_added(last_added).isoformat()
            mw.addonManager.writeConfig(__name__, CONFIG)    

            
def hasDuplicateHighlightMatches(clipping, vocabs):
    seen = {}
    dupes = []

    for vocab in vocabs:
        if clipping.content not in vocab.usage:
            continue
        if clipping.content == '■':
            showInfo(str([vocab.usage for vocab in vocabs]))
        if vocab.usage not in seen:
            seen[vocab.usage] = True
        else:
            if seen[vocab.usage] == True:
                dupes.append(vocab)
    if clipping.content == '■':
        showInfo( str(dupes))
    if len(dupes) >= 1:
        return dupes[-1]
    return False



def after_last_added(clippings, last_added):
    if not last_added:
        return clippings

    def reversed_clippings_after_last_added():
        for clipping in reversed(clippings):
            if clipping.added:
                clipping_added = parse_clipping_added(clipping.added)
                if clipping_added and clipping_added <= last_added:
                    return
            yield clipping

    clippings_after_last_added = list(reversed_clippings_after_last_added())
    clippings_after_last_added.reverse()
    return clippings_after_last_added


def parse_clipping_added(clipping_added):
    return datetime.strptime(clipping_added, '%Y年%m月%d日%A %H:%M:%S')


def last_added_datetime():
    last_added_config = CONFIG['last_added']
    return datetime.strptime(last_added_config, '%Y-%m-%dT%H:%M:%S') if last_added_config else None



def create_connection():
    path = os.path.join(CONFIG['path'], 'system', 'vocabulary', 'vocab.db')
    return sqlite3.connect(path)

def getTimestamp():
    longAgo = 1362301382
    if CONFIG['last_added']:
        # time since last round, minus a day
        ts = datetime.strptime(CONFIG['last_added'], '%Y-%m-%dT%H:%M:%S').timestamp() - 86400
    else:
        ts = longAgo
    return ts

def convertToVocab(rows):
    vocabs = []
    for row in rows:
        try:
            vocabs.append()
        except:
            pass
    return vocabs


def getVocabLookups():
    conn = create_connection()
    # sqlite3.OperationalError: Could not decode to UTF-8 column 'usage' with text; Happens with blob data?
    conn.text_factory = lambda b: b.decode(errors = 'ignore')
    cur = conn.cursor()
    timestamp = getTimestamp()
    sql = f'''
    select WORDS.stem, WORDS.word, LOOKUPS.usage, LOOKUPS.timestamp, BOOK_INFO.title, BOOK_INFO.authors
    from LOOKUPS left join WORDS
    on WORDS.id = LOOKUPS.word_key
    left join BOOK_INFO
    on BOOK_INFO.id = LOOKUPS.book_key
    WHERE DATETIME(LOOKUPS.timestamp/1000, 'unixepoch') > DATETIME({timestamp}, 'unixepoch')
	ORDER BY LOOKUPS.timestamp DESC;
    '''
    cur.execute(sql)
    
    return [Vocab(*row) for row in cur.fetchall()]

def getTimestampDistance(clipping, vocab):
    
    clippingTimestamp = parse_clipping_added(clipping.added).timestamp()
    return abs(clippingTimestamp - vocab.timestamp/1000)


def getVocab(clipping, vocabs):
    foundVocab = None
    possibleUsages = []
    distances = []
    for index, vocab in enumerate(vocabs):
        if clipping.content in vocab.usage:
            distance = getTimestampDistance(clipping, vocab)
            possibleUsages.append(vocab)
            distances.append(distance)
            
    if possibleUsages:
        minIndex = distances.index(min(distances))
        foundVocab = possibleUsages[minIndex]
        vocabDebug("distance", vocabs, clipping, foundVocab, distances)

    return foundVocab, vocabs


def isUnique(newNote, pendingNotes):
    sentence_field = CONFIG['sentence_field']
    word_field = CONFIG['word_field']
    for pendingNote in pendingNotes:
        pn = pendingNote['note']
        if newNote[sentence_field] == pn[sentence_field] and newNote[word_field] == pn[word_field]:
            return False
    return True


def setupCache():
    global VALID_WORDS
    VALID_WORDS = Words()
    return VALID_WORDS


def removeCache(cache):
    del cache

def showProgressOrFinish(update=False, **kwargs):
    if not DEBUG:
        if update:
            mw.progress.update(**kwargs)
        elif kwargs:
            mw.progress.start(**kwargs)
        else:
            mw.progress.finish()




def import_highlights():
    model = mw.col.models.byName(CONFIG['model_name'])
    if not model:
        showInfo(f'Your model_name of "{CONFIG["model_name"]}" is not a valid Note Type and does not exist in your collection.\n\nPlease use a valid Note Type. You can refer to the Anki Manual on it here: https://docs.ankiweb.net/#/editing?id=adding-a-note-type')
        return
    n = Note(mw.col, model)
    for fieldName in ['sentence_field', 'source_field', 'word_field']:
        if CONFIG[fieldName] not in n:
            showInfo(f'Your Note Type of {CONFIG["model_name"]} does not contain a field named {CONFIG[fieldName]}')
            return
    
    # mw.progress.start(label='Scanning Highlights...\n ', min=1, immediate=True)
    showProgressOrFinish(label='Scanning Highlights...\n ', min=1, immediate=True)
    path = os.path.join(CONFIG['path'], 'documents', 'My Clippings.txt')
    try:
        highlight_clippings, clippings_to_add, bad_clippings, clippings = getClippings(path)
    except FileNotFoundError:
        # mw.progress.finish()
        showProgressOrFinish()
        showInfo(f'Your file path to your Kindle could not be loaded. Does this file exist: {path} ?')
        return

    cache = setupCache()
    timestamp = None
    no_vocab = []
    vocabs = getVocabLookups()
    vocabDebug("original", vocabs)
    clippings_to_add.reverse()
    # mw.progress.update(label='Parsing New Highlights...\n ')
    showProgressOrFinish(True, label='Parsing New Highlights...\n ')
    pendingNotes = []
    for i, clipping in enumerate(clippings_to_add):
        # mw.progress.update(label=f'Parsing New Highlights...\n {clipping.content}', value=i+1)
        showProgressOrFinish(True, label=f'Parsing New Highlights...\n {clipping.content}', value=i+1)
        note = Note(mw.col, model)
        # showInfo(str(len(vocabs)))
        vocabDebug("before", vocabs, clipping)
        vocab, vocabs = getVocab(clipping, vocabs)
        if not vocab:
            no_vocab.append(str(clipping))
            vocabDebug("notFound", vocabs, clipping)
            continue            
        # showInfo(clipping.content +' '+ str(vocab))
        vocabDebug("after", vocabs, clipping, vocab)

        note.fields = list(fields(clipping, model, vocab))
        note.addTag(vocab.authors)
        note.addTag(vocab.title)
        if not pendingNotes or isUnique(note, pendingNotes):
            pendingNotes.append({"note":note,"vocab":vocab})
    
    # Create them in the order they were read
    pendingNotes.reverse()
    for pendingNote in pendingNotes:
        pn = pendingNote['note']
        mw.col.addNote(pn)
        cids = [c.id for c in pn.cards()]
        dm = DeckManager(mw.col)
        deckId = getDeck(pendingNote['vocab'])
        dm.setDeck(cids, deckId)
        pn.flush()

    showProgressOrFinish()
    # mw.progress.finish()

    if no_vocab :
        showText(
            f'The following {len(no_vocab)} clippings could not be matched automatically:\n\n' +
            '\n==========\n'.join(no_vocab))

    if clippings_to_add:
        setLastAdded(clippings_to_add[0].added)
    displayResults(highlight_clippings, clippings_to_add, bad_clippings, clippings, pendingNotes)
    
    removeCache(cache)



def parse_text_clippings(file):
    clippings = []
    bad_clippings = []

    current_clipping_lines = []
    for line in file:
        if line != '==========\n':
            current_clipping_lines.append(line)
            continue

        string = ''.join(current_clipping_lines)
        current_clipping_lines.clear()

        clipping = parse_text_clipping(string)

        if clipping:
            # get around blank highlights; seems to be a kindle bug; Also don't want to bug the user with calling it a bad_clipping
            if clipping.content:
                clippings.append(clipping)
        else:
            if "ブックマーク" not in string:
                bad_clippings.append(string)

    if current_clipping_lines:
        bad_clippings.append(''.join(current_clipping_lines))

    return clippings, bad_clippings


def parse_text_clipping(string):

    match = re.fullmatch(CLIPPING_PATTERN, string)
    if not match:
        return None
    return Clipping(**match.groupdict())

CLIPPING_PATTERN = r'''\ufeff?(?P<document>.*)
- ((?P<page>.*)?ページ\|)?位置No\. (?P<location>.*)?の(?:(?P<kind>.*) \|)?作成日: (?P<added>.*)

(?P<content>.*)
?'''

# It could be bookmarks too - which would break
def highlights_only(clippings):
    for clipping in clippings:
        if 'ハイライト' in clipping.kind.lower():
            yield clipping

def deinflectVocab(vocab):

    if VALID_WORDS.contains(vocab):
        return vocab

    # Use basic deconjugation rules to guess a word
    deconjugations = deconjugate(vocab)
    for dc in deconjugations:
        if VALID_WORDS.contains(dc):
            return dc
    
    # Resort to mecab breaking things into individual words
    try:
        splitter = Splitter()
        wordItems = splitter.analyze(vocab)
        # if vocab != wordItems:
        #     showInfo(vocab + ' ' + str(wordItems))
        global MECABHITS 
        MECABHITS += 1
        return wordItems
        # for splitWord in wordItems[1::2]:
        #     # print(splitWord, VALID_WORDS.contains(splitWord))
        #     if VALID_WORDS.contains(splitWord):
        #         # if vocab == 'ぎいぎいと':
        #         showInfo(str(wordItems) + ' '+ splitWord)
        #         return splitWord
    except Exception as e:
        pass
        raise Exception(str(e)+"\nCan't do sentence scan: check Japanese Support is installed and working properly")



    return vocab

def removeExtraChars(v):
    regex = u'([\u4E00-\u9FFF]|[\u3040-\u309Fー]|[\u30A0-\u30FF])+'
    match = re.search(regex, v, re.U)
    try:
        return match[0]
    except TypeError: # things like ａｍｐｍ
        return v

def cleanVocab(v):
    # cleaned = "".join(c for c in v if c not in BLACKLIST)
    cleaned = removeExtraChars(v)
    deinflected = deinflectVocab(cleaned)
    return deinflected

def fields(clipping, model, vocab):
    content_yielded = False
    source_yielded = False
    word_yielded = False

    for field in mw.col.models.fieldNames(model):
        if field == CONFIG['sentence_field']:
            yield vocab.usage.strip()
            content_yielded = True
        elif field == CONFIG['source_field']:
            pg = 'ページ' + clipping.page if clipping.page is not None else ''
            loc = '位置' + clipping.location if clipping.location is not None else ''
            yield '{page}{added}{word}'.format(
                page= pg if pg else loc,
                added=' ' + clipping.added if clipping.added is not None else '',
                word=' ' + clipping.content
            )
            source_yielded = True
        elif field == CONFIG['word_field']:
            yield cleanVocab(clipping.content)
            word_yielded = True
        else:
            yield ''

    if not (content_yielded and source_yielded and word_yielded):
        raise ValueError('Could not find content and/or source fields in model.')


# import kindleImporter
# from importlib import reload
# reload(kindleImporter)
# reload(kindleImporter.splitter)
# from kindleImporter.splitter import deconjugate, Splitter, Words

# print(deconjugate('食べて窮する'))
# print(Splitter().analyze('食べて窮する'))
# d = Words()
# print(d._dic['窮する'])
# print(d.contains('窮し'))
def test():
    vocab = cleanVocab('雲散霧消')
    # print(vocab)
    assert vocab == '雲散霧消'

    vocab = cleanVocab('ばけた')
    assert vocab == 'ばける'

    # ideally we could do get 身代わり
    vocab = cleanVocab('身がわり')
    assert vocab == '身'
    
    vocab = cleanVocab('ひとえに')
    assert vocab == 'ひとえに'

    # Currently bad mecab parsing
    vocab = cleanVocab('窮して、')
    assert vocab == '窮す'

    vocab = cleanVocab('「歯がうく、何')
    assert vocab == '歯がうく'

    vocab = cleanVocab('コロコロ')
    assert vocab == 'コロコロ'
