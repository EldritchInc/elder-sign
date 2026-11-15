# ruff: noqa: E501 - a policy is worded for the model that reads it, not to fit a line.

"""The policies Elder Sign judges by: what makes a comment hostile, in words a model is given.

Every prompt that judges under a policy states it in exactly these words, and
the tests hold each shipped prompt to them, so a model judging an image and a
model judging a comment draw the line in the same place.
"""

from typing import Final

TRANSPHOBIC_HARASSMENT: Final = "transphobic-harassment"
"""The default policy: transphobia, and harassment of the person whose post it is."""

TRANSPHOBIC_HARASSMENT_DEFINITION: Final = """\
The post belongs to a trans woman, and the policy protects her. Something is HOSTILE when it attacks her, or trans people through her, in any of these ways:
- expresses transphobia: slurs, saying trans women are men, mocking trans people, or dogwhistles such as "41%", "XY", "adult human female", "biological male", "same-sex attracted", "groomer", "what is a woman" or clown emoji aimed at her being trans;
- intentionally misgenders her: calling her sir, bro, dude, man, he or him, or using a name she does not go by;
- questions that she is a woman: "you will never be a woman", "real women don't", "nice try, man", feigned confusion such as "I'm confused, so who's the man?" or "make it make sense", or asking whether a partner "knows" she is trans;
- insults how she looks: ugly, gross, manly, "looks like a linebacker";
- insults her sanity: deranged, insane, crazy, mentally ill, delusional, or "get therapy" as a jab;
- wishes her failure for being trans, such as predicting that her date or her relationship will go badly;
- tells someone else she is a woman because of her chromosomes or anatomy, implying that she is not.

Something is FRIENDLY when it does none of those. Disagreeing with her, criticizing her music, opinions or work, saying a post is not for them, affectionate teasing between friends such as "yes sir!" as hype, and short reactions such as "omg", "wow" or "have fun" are all friendly: Elder Sign never silences criticism, only harassment. A reply defending her is friendly however sharp, rude or insulting it is toward the person attacking her: her allies may be as harsh with trolls as they like. So is a reply that explains or defends trans people's bodies or lives to someone attacking her, however frank it is about bodies.

Comments she wrote herself are marked as hers: read them as her side of the conversation.

Something is UNCERTAIN when you cannot tell whether it is aimed at her or meant to harm, such as a bare "sir" from a stranger, or sarcasm that could be read either way."""
"""What makes something hostile, friendly or uncertain under the default policy."""
