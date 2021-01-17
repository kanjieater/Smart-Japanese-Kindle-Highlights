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


locale.setlocale(locale.LC_ALL, 'ja_JP')

CONFIG = mw.addonManager.getConfig(__name__)

Clipping = namedtuple('Clipping', ('kind', 'document', 'page', 'location', 'added', 'content'))
Vocab = namedtuple('Vocab', ('stem', 'word', 'usage', 'timestamp', 'title', 'authors'))


def getDeck(vocab):
    # deck = mw.col.decks.byName(CONFIG['deck_name'])    
    return mw.col.decks.id(CONFIG['deck_name'] + '::' + vocab.title)
    # did = deck['id']
    # if not did:
    #     showInfo(CONFIG['deck_name'] + ' was not found. Please check the deck name in the config.')
    #     return
    # return did


def getClippings():
    path = os.path.join(CONFIG['path'], 'documents', 'My Clippings.txt')
    with open(path, encoding='utf-8') as file:
        lower_path = path.lower()
        if lower_path.endswith('txt'):
            clippings, bad_clippings = parse_text_clippings(file)
        elif lower_path.endswith('html'):
            clippings, bad_clippings = parse_html_clippings(file)
        else:
            raise RuntimeError(f'Unknown extension in path: {path!r}')

    if bad_clippings:
        showText(
            f'The following {len(bad_clippings)} clippings could not be parsed:\n\n' +
            '\n==========\n'.join(bad_clippings))

    highlight_clippings = list(highlights_only(clippings))
    clippings_to_add = after_last_added(highlight_clippings, last_added_datetime())
    return highlight_clippings, clippings_to_add, bad_clippings, clippings


def displayResults(highlight_clippings, clippings_to_add, bad_clippings, clippings, no_vocab):
    def info():
        if clippings_to_add:
            yield f'{len(clippings_to_add)-len(no_vocab)} new highlights imported'

        num_old_highlights = len(highlight_clippings) - len(clippings_to_add)
        if num_old_highlights:
            yield f'{num_old_highlights} old highlights ignored'

        num_not_highlights = len(clippings) - len(highlight_clippings)
        if num_not_highlights:
            yield f'{num_not_highlights} non-highlight clippings ignored'

    info_strings = list(info())
    if info_strings:
        showInfo(', '.join(info_strings) + '.')
    elif bad_clippings:
        showInfo('No other clippings found.')
    else:
        showInfo('No clippings found.')


def setLastAdded(last_added):
    if last_added:
        CONFIG['last_added'] = parse_clipping_added(last_added).isoformat()
        # CONFIG['last_added'] = CONFIG['last_added']
        mw.addonManager.writeConfig(__name__, CONFIG)        


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

def getVocabLookups():
    cur = create_connection().cursor()
    timestamp = getTimestamp()
    # showInfo(str(timestamp))
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
    vocabs = [Vocab(*row) for row in cur.fetchall()]
    # showInfo(str(len(vocabs)))
    return vocabs

def getVocab(clipping, vocabs):
    for index, vocab in enumerate(vocabs):
        # TODO Oof need actual deconjugation for Japanaese
        # if you looked up other words in the same sentence you might get a false vocab entry
        if clipping.content[0] == vocab.stem[0] and clipping.content in vocab.usage:
            return vocab, vocabs[index:]

    # Double check for loose matches (where word doesn't match highlight eg ころころｖｓコロコロ)
    for index, vocab in enumerate(vocabs):
        if clipping.content in vocab.usage:
            return vocab, vocabs
    return None, vocabs



def import_highlights():
    model = mw.col.models.byName(CONFIG['model_name'])
    # last_added = None
    # did = getDeck()
    highlight_clippings, clippings_to_add, bad_clippings, clippings = getClippings()
    
    timestamp = None
    no_vocab = []
    vocabs = getVocabLookups()
    clippings_to_add.reverse()
    for clipping in clippings_to_add:
        note = Note(mw.col, model)
        
        vocab, vocabs = getVocab(clipping, vocabs)
        if not vocab:
            no_vocab.append(str(clipping))

            continue            
        # showInfo(clipping.content +' '+ str(vocab))
        
        note.fields = list(fields(clipping, model, vocab))
        
        note.addTag(vocab.authors)
        note.addTag(vocab.title)
        mw.col.addNote(note)
        cids = [c.id for c in note.cards()]
        dm = DeckManager(mw.col)
        deckId = getDeck(vocab)
        dm.setDeck(cids, deckId)
        note.flush()

        # if clipping.added:
        #     last_added = clipping.added
    if no_vocab :
        showText(
            f'The following {len(no_vocab)} clippings could not be matched automatically:\n\n' +
            '\n==========\n'.join(no_vocab))

    if clippings_to_add:
        setLastAdded(clippings_to_add[0].added)
    displayResults(highlight_clippings, clippings_to_add, bad_clippings, clippings, no_vocab)




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
            clippings.append(clipping)
        else:
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
- (?P<page>.*)?ページ\|位置No\. (?P<location>.*)?の(?:(?P<kind>.*) \|)?作成日: (?P<added>.*)

(?P<content>.*)
?'''


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


# IDK what else this could be. OR if if it's necessary
def highlights_only(clippings):
    for clipping in clippings:
        if 'ハイライト' in clipping.kind.lower():
            yield clipping


def fields(clipping, model, vocab):
    content_yielded = False
    source_yielded = False
    word_yielded = False

    for field in mw.col.models.fieldNames(model):
        if field == CONFIG['sentence_field']:
            yield vocab.usage.strip()
            content_yielded = True
        elif field == CONFIG['source_field']:
            yield '{page}{added}{word}'.format(
                page='ページ' + clipping.page if clipping.page is not None else '',
                added=' ' + clipping.added if clipping.added is not None else '',
                word=' ' + vocab.word
            )
            source_yielded = True
        elif field == CONFIG['word_field']:
            yield vocab.stem
            word_yielded = True
        else:
            yield ''

    if not (content_yielded and source_yielded and word_yielded):
        raise ValueError('Could not find content and/or source fields in model.')

