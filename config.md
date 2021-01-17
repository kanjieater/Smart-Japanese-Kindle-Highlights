`model_name` is the name of the model to use for notes created from highlights.

`content_field` is the name of the field in which to put the content of the highlight.

`source_field` is the name of the field in which to put the source of the highlight.

`last_added` is the time of the last highlight which was added to Anki.
Highlights from before this time will not be re-added.
Set it to null to add all highlights again.

`path` the path to your Kindle. The add-on uses path to find `path` + `/documents/MyClippings.txt` AND the hidden system folder `path` + `/system/vocabulary/vocab.db`
