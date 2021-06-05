`model_name` is the name of the model to use for notes created from highlights.

`word_field` is the name of the field in which to put the word of the highlight.

`sentence_field` is the name of the field in which to put the sentence containing the highlight.

`source_field` is the name of the field in which to put the source of the highlight.

`last_added` is the time of the last highlight which was added to Anki.
Highlights from before this time will not be re-added.
Set it to null to add all highlights again.

`deck_name` is the name of the deck where the highlighted word cards will be placed into. The add-on will make subdecks by the name of the book and place them here.

`path` the path to your Kindle. The add-on uses path to find `path` + `/documents/MyClippings.txt` AND the hidden system folder `path` + `/system/vocabulary/vocab.db`
