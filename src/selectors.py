"""Centralised CSS / text selectors for the LinkedIn DOM.

WARNING: LinkedIn's DOM changes frequently. These selectors are best-effort
against the current LinkedIn markup and WILL require periodic maintenance.
They favour stable attributes where possible (``data-urn``, ``role="dialog"``,
``aria-label``, ``href*="/in/"``) and combine multiple comma-separated
fallbacks per key so the scraper has more than one chance to match.

The LinkedIn UI may render in FRENCH or ENGLISH. Selectors here therefore rely
on structural / attribute hooks rather than visible text wherever possible.
Where an ``aria-label`` substring is matched, the ``i`` flag (case-insensitive)
is used, but localized labels can still differ; the scraper compensates with
additional fallbacks and graceful empty-result handling.

This module has NO imports and exposes a single ``SELECTORS`` mapping.
"""

from __future__ import annotations

SELECTORS: dict[str, str] = {
    # --- Activity feed: post containers ---
    # Each post card on the author's feed. Covers BOTH a company "/posts/" feed
    # and a profile "recent-activity/all" feed: they share the same update
    # components. data-urn is the most stable hook and carries the activity URN
    # used to build a permalink, so the trailing attribute selector matches
    # either layout.
    "post_container": (
        'div.feed-shared-update-v2[data-urn], '
        'div.fie-impression-container[data-urn], '
        '[data-urn^="urn:li:activity"]'
    ),

    # --- Post relative time text ---
    # The relative date ("2mo", "il y a 3 sem.") shown in the post header.
    "post_time": (
        "span.update-components-actor__sub-description, "
        ".update-components-actor__sub-description, "
        "time"
    ),

    # --- "See all reactions" opener (opens the full reactors modal) ---
    # LinkedIn now ships obfuscated, hashed class names, so we anchor on the
    # localized aria-label of the social-proof summary. This opens the modal
    # listing EVERY reactor. It is NOT the Like toggle nor the reaction picker.
    "reactions_button": (
        '[aria-label="Voir toutes les réactions"], '
        '[aria-label*="toutes les réaction" i], '       # FR fallback
        '[aria-label*="all reactions" i], '             # EN "View/See all reactions"
        '[aria-label*="see who reacted" i]'
    ),

    # In-page reactor facepile (top reactors, no click needed). The data-testid
    # survives class obfuscation and is the reliable fallback / baseline source.
    "reaction_facepile": '[data-testid^="ReactionFacepileCollection"]',

    # --- Reactions modal ---
    # The reactors list is a NATIVE <dialog> element (data-testid="dialog"), not
    # a div[role="dialog"]. It only gets the `open` attribute once the opener is
    # clicked, so `dialog[open]` reliably means "reactions modal is open".
    "reactions_dialog": 'dialog[open], [data-testid="dialog"][open], [role="dialog"]',

    # A reactor entry, identified by its accessible label
    # "<Name> a réagi avec <Type>[, <degree>, <headline>]" (FR) /
    # "<Name> reacted with <Type>…" (EN). Used in BOTH the facepile and the
    # modal — it carries the name (and, in the modal, the headline).
    "reactor_info": (
        '[aria-label*="a réagi avec" i], '
        '[aria-label*="a reagi avec" i], '
        '[aria-label*="reacted with" i]'
    ),

    # Anchor linking to a reactor's profile (people only -> /in/ path).
    "reactor_link": 'a[href*="/in/"]',

    # --- Reposts (read from the FEED only; the detail page hides repost data) ---
    # Opener for the LIST of people who reposted a post. LinkedIn shows the
    # repost total on the feed card; clicking the count opens a modal/list of the
    # reshares. We anchor on a localized aria-label that names a repost LIST or
    # count (plural / digit-bearing).
    #
    # SAFETY: this may incidentally match the "Republier" / "Repost" composer
    # ACTION button (clicking that reshares the post AS THE LOGGED-IN USER), so it
    # is NOT the safety boundary on its own. ``parser.is_safe_reposts_opener``
    # rejects any candidate whose label carries a composer verb word — even one
    # bearing a count ("Republier 14") — and requires a positive reposts-list noun
    # before a click. The two layers together guarantee we never repost as the user.
    # NOTE: the "partage"/"partager" comment-box controls ("Partager une photo",
    # emoji picker) also carry that substring but are correctly rejected by
    # ``parser.is_safe_reposts_opener`` (they contain the composer verb), so this
    # list is deliberately narrowed to the repost count nouns to avoid matching
    # them at all.
    "reposts_button": (
        'a[aria-label*="republication" i], '          # FR "N republications"
        'button[aria-label*="republication" i], '
        'a[aria-label*="reposts" i], '                # EN "N reposts"
        'button[aria-label*="reposts" i], '
        'a[aria-label*="repost" i], '
        'button[aria-label*="repost" i]'
    ),

    # The reposts list opens as LinkedIn's Ember "artdeco" modal overlay
    # (``div.artdeco-modal-overlay`` / ``[data-test-modal-container]``), NOT the
    # native ``<dialog>`` the reactions modal uses — confirmed from a live run.
    # The native-dialog variants are kept first in case LinkedIn migrates this
    # surface. The broad ``[role="dialog"]`` fallback is DELIBERATELY omitted (it
    # would match unrelated feed panels and yield bogus reposters); the scraper
    # also dismisses any open overlay BEFORE each opener click, so a match seen
    # AFTER the click is the reposts modal we just opened.
    "reposts_dialog": (
        'dialog[open], '
        '[data-testid="dialog"][open], '
        'div.artdeco-modal-overlay, '
        '[data-test-modal-container]'
    ),

    # Close/dismiss control inside an open modal overlay (native or artdeco),
    # localized FR/EN. Used to force-close a leaked reposts overlay so it cannot
    # intercept the next card's opener click.
    "overlay_close": (
        'button[aria-label*="Fermer" i], '
        'button[aria-label*="Ignorer" i], '
        'button[aria-label*="Close" i], '
        'button[aria-label*="Dismiss" i]'
    ),

    # One repost entry (the reshare itself) inside the reposts modal. Confirmed
    # from a live capture: each reshare carries its own activity ``data-urn`` and
    # ``role="article"`` (the embedded ORIGINAL it quotes does not), so iterating
    # these yields exactly one row per repost.
    "repost_entry": '[data-urn], [role="article"]',

    # The embedded ORIGINAL post quoted inside a repost entry. CRITICAL: everything
    # inside it (the original author, its reaction facepile, its commenters,
    # @-mentions) must be EXCLUDED when reading the reposter — otherwise those
    # people are miscounted as reposters. A single repost entry was observed to
    # contain ~11 ``/in/`` links of which only ONE is the reposter.
    "repost_embedded_original": (
        '[class*="update-components-mini-update" i], '
        '[class*="feed-shared-mini-update" i]'
    ),

    # A reposter entry's profile link (the reposter is the entry's actor, /in/
    # path). The extractor takes the first such link that is NOT inside
    # ``repost_embedded_original`` for the profile identity.
    "reposter_info": 'a[href*="/in/"]',

    # The reposter's display NAME element (LinkedIn's non-hashed actor-title
    # component holds just the name — no degree/headline, unlike the actor link's
    # combined text). The extractor reads the first one OUTSIDE the embedded
    # original, falling back to the actor link's text when absent.
    "reposter_name": '[class*="update-components-actor__title" i]',

    # The reposter's OWN added commentary (post-body text of the repost entry,
    # outside the embedded original). Its presence marks a reshare-WITH-a-comment;
    # its absence, a plain reshare. ``update-components-text`` is the durable
    # (non-hashed) body-text hook confirmed from a live reposts modal.
    "repost_comment_marker": (
        '[class*="update-components-text" i], '
        '[class*="commentary" i]'
    ),

    # --- Comments ---
    # Per-comment "more options" control. Its aria-label names the author
    # ("Voir plus d'options pour le commentaire de <Name>" FR / "…<Name>'s
    # comment" EN), so there is exactly one per comment — the count + author
    # hook. The comment editor label is "…créer un commentaire" (no " de "), so
    # it is not matched.
    "comment_author_info": (
        '[aria-label*="commentaire de" i], '
        '[aria-label*="commentaire par" i], '
        '[aria-label*="comment by" i], '
        '[aria-label*="comment from" i], '
        "[aria-label*=\"'s comment\" i], "
        '[aria-label*="’s comment" i]'
    ),
    # Controls that reveal more comments / replies. Deliberately narrow so the
    # reply/comment ACTION buttons (Répondre, Commenter, J'aime) are NEVER
    # matched — clicking those would open an editor or react as the user.
    "load_more_comments": (
        'button[aria-label*="plus de commentaire" i], '
        'button[aria-label*="more comment" i], '
        'button[aria-label*="commentaires précédent" i], '
        'button[aria-label*="previous comment" i], '
        'button[aria-label*="afficher"][aria-label*="réponse" i], '
        'button[aria-label*="voir"][aria-label*="réponse" i], '
        'button[aria-label*="more repl" i], '
        'button[aria-label*="previous repl" i]'
    ),
}
