# ruff: noqa: E501 - a phrase table: one sentence per line reads better than wrapped
"""English for the desk: the sentences code writes in Greek, turned into English when the desk asks for it.

One place, a fixed table: each rule matches a whole sentence that code produces (with numbers, places and
quotes captured) and writes it in English. What no rule matches stays as it is: citizens' messages, street and
place names, quotes from messages, and free text an agent wrote. Nothing here is a model; nothing is guessed."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Words that code puts into sentences.
VULN = {"ηλικιωμένοι": "elderly", "παιδιά": "children", "κινητικά προβλήματα": "mobility problems",
        "κατάκοιτος": "bedridden", "έγκυος": "pregnant", "αναπνευστικό": "respiratory", "εγκαύματα": "burns",
        "τραυματισμός": "injury", "χωρίς όχημα": "no vehicle", "ζώα": "animals"}
NEEDS = {"χωρίς αισθήσεις": "unconscious", "δυσκολία στην αναπνοή": "difficulty breathing", "εγκαύματα": "burns",
         "καρδιά": "heart", "τραυματισμός": "injury", "οξυγόνο/ινσουλίνη/συσκευή": "oxygen/insulin/device",
         "βρέφος": "infant", "έγκυος": "pregnant", "δεν περπατά": "cannot walk", "κατάκοιτος": "bedridden",
         "ηλικιωμένος/η": "elderly", "παιδιά": "children", "ζώα": "animals", "φάρμακα": "medication",
         "ανάγκη υγείας": "a health need"}
DECISION = {"εκκένωση": "evacuation", "αποστολή μονάδας": "send a unit", "παραμονή σε κτίριο": "stay in a building",
            "διάσωση από θάλασσα": "rescue from the sea", "επανάκληση": "call back", "έλεγχος": "review",
            "απομάκρυνση": "leave the area", "πληροφορία": "information", "αγνοούμενος": "missing person",
            "παρακολούθηση": "monitor", "ασφαλείς": "safe", "εκτός περιοχής": "outside the area",
            "οδηγίες απομάκρυνσης": "directions to leave"}
KIND = {"πυροσβεστικό όχημα": "fire engine", "πυροσβεστικό": "fire engine", "ασθενοφόρο": "ambulance"}
SHORE = {"γκρεμός": "cliff", "παραλία": "beach", "ομαλή ακτή": "gentle shore", "ακτή": "shore"}
UNIT_STATE = {"χωρίς αναφορά": "no report", "σχεδιασμένη": "planned", "ανατέθηκε": "assigned", "καθ' οδόν": "on the way",
              "επί τόπου": "on scene", "επιστρέφει": "returning", "διαθέσιμη": "available", "βλάβη": "breakdown",
              "εκτός υπηρεσίας": "out of service"}

# Fixed sentences.
EXACT: dict[str, str] = {
    # proposal reasons (pipeline/decide.py)
    "Το μήνυμα προσπαθεί να δώσει εντολές στο σύστημα.": "The message tries to give the system orders.",
    "Ανεπιβεβαίωτος ισχυρισμός· χρειάζεται διασταύρωση.": "Unconfirmed claim; it needs cross-checking.",
    "Πληροφορία για την κατάσταση, όχι αίτημα διάσωσης.": "Information about the situation, not a request for rescue.",
    "Ο αποστολέας αναφέρει ότι είναι ασφαλής.": "The sender says they are safe.",
    "Αναφορά αγνοούμενου· στην επανένωση οικογενειών.": "A missing person report; for family reunification.",
    "Το μήνυμα είναι ελλιπές.": "The message is incomplete.",
    "Πού βρίσκεστε και πόσα άτομα είστε;": "Where are you and how many are you?",
    "Αναφέρει φωτιά κοντά ή ανθρώπους στην ακτή: αντιμετωπίζεται ως αίτημα βοήθειας.":
        "It reports fire nearby or people on the shore: treated as a request for help.",
    "Το μοντέλο φωτιάς δεν φτάνει εδώ, αλλά το μήνυμα αναφέρει φωτιά κοντά: εμπιστευόμαστε την αναφορά.":
        "The fire model does not reach here, but the message reports fire nearby: we trust the report.",
    "Το ανασυνθεμένο μέτωπο δεν φτάνει σε αυτό το σημείο.": "The reconstructed front does not reach this point.",
    "Το ανασυνθεμένο μέτωπο δεν περνά από το ίδιο το σημείο του δρόμου· μετράει το πιο κοντινό σημείο του που "
    "καίγεται (για ασφάλεια).": "The reconstructed front does not cross this point of the street; the nearest "
                               "burning point of it counts (to be safe).",
    "Ερώτηση χωρίς περιγραφή κινδύνου: ενημέρωση και ετοιμότητα.":
        "A question with no danger described: inform and keep ready.",
    "Άνθρωποι στην ακτή/θάλασσα· το ακριβές σημείο θέλει επιβεβαίωση.":
        "People on the shore or in the sea; the exact point needs confirming.",
    "Δεν είναι γνωστή η ακριβής θέση.": "The exact location is not known.",
    "Άνθρωποι στην ακτή/θάλασσα.": "People on the shore or in the sea.",
    "Δεν μπορούν να φύγουν μόνοι τους.": "They cannot leave on their own.",
    "Μόνο η περιοχή είναι γνωστή: η διεύθυνση ζητείται παράλληλα.":
        "Only the area is known: the address is being asked for at the same time.",
    "Ζητούν διαδρομή και μπορούν να κινηθούν· μόνο η περιοχή είναι γνωστή, η διεύθυνση ζητείται παράλληλα.":
        "They ask for a route and can move; only the area is known, the address is being asked for meanwhile.",
    "Μόνο η περιοχή είναι γνωστή.": "Only the area is known.",
    "Να παραμείνουν σε ετοιμότητα.": "They should stay ready.",
    "Έχουν κλειστεί σε χώρο· χρειάζεται μονάδα.": "They are shut in; a unit is needed.",
    "Δεν μπορούν να απομακρυνθούν μόνοι τους.": "They cannot get away on their own.",
    "Αναφορά από τρίτον: οι άνθρωποι στο σημείο δεν επικοινωνούν μαζί μας.":
        "Reported by someone else: the people there are not in touch with us.",
    "Σε όχημα με το μέτωπο κοντά: η οδική διαφυγή μπορεί να μην προλαβαίνει.":
        "In a vehicle with the front close: escape by road may be too late.",
    # arbiter
    "Οι πράκτορες δεν διαφωνούν.": "The agents do not disagree.",
    "Εντολή προς το σύστημα μέσα στο μήνυμα: έλεγχος από άνθρωπο.":
        "Orders to the system inside the message: a person reviews it.",
    "Ο έλεγχος αξιοπιστίας το κρίνει πιθανώς ψευδές: επαλήθευση πριν μετακινηθεί κανείς.":
        "The credibility check finds it likely false: verify before anyone moves.",
    "Αμφίβολο μήνυμα, αλλά ο κριτής κινδύνου πρότεινε ασφαλέστερη απόφαση: ισχύει αυτή.":
        "A doubtful message, but the hazard critic proposed a safer decision: that one stands.",
    "Αμφίβολο μήνυμα: η πρόταση μένει (πρώτα η ζωή), αλλά επιβεβαίωσε με τον αποστολέα πριν από την αποστολή.":
        "A doubtful message: the proposal stands (life first), but confirm with the sender before sending.",
    "Η περιοχή συμπεράνθηκε ανάμεσα σε ομώνυμους δρόμους: η πρόταση μένει, επιβεβαίωσε την περιοχή.":
        "The area was inferred among streets with the same name: the proposal stands, confirm the area.",
    # credibility
    "Ο έλεγχος αξιοπιστίας δεν έτρεξε· δες τα σήματα.": "The credibility check did not run; see the signals.",
    "Ζητά χρήματα ή δίνει σύνδεσμο.": "It asks for money or gives a link.",
    "Η οδός δεν υπάρχει πουθενά στην περιοχή.": "The street exists nowhere in the area.",
    "Μεταφέρει κάτι που άκουσε, όχι κάτι που βλέπει.": "It passes on something heard, not something seen.",
    # hazard facts
    "Κάποιος δεν μπορεί να περπατήσει ή είναι κατάκοιτος.": "Someone cannot walk or is bedridden.",
    "Δεν έχουν όχημα.": "They have no vehicle.",
    "Αναφέρουν καπνό εκεί που βρίσκονται.": "They report smoke where they are.",
    "Η θέση είναι δίπλα σε γκρεμό στην ακτή.": "The location is next to a cliff on the shore.",
    # missions
    "άνθρωποι σε κίνδυνο χωρίς αναφορά τραυματισμού": "people in danger, no injury reported",
    "Η θέση απέχει πολύ από το οδικό δίκτυο: καμία μονάδα ξηράς δεν μπορεί να φτάσει.":
        "The location is far from the road network: no ground unit can get there.",
    "Κάθε δρόμος προς τη θέση καίγεται τώρα: καμία μονάδα ξηράς δεν φτάνει με ασφάλεια.":
        "Every road to the location is burning now: no ground unit gets there safely.",
    "Καμία διαθέσιμη μονάδα.": "No unit available.",
    "η θέση είναι σωστή (διεύθυνση και ακρίβεια στον χάρτη)": "the location is right (address and precision on the map)",
    "είδα τη διαδρομή και πότε φτάνει εκεί το μέτωπο": "I saw the route and when the front reaches it",
    "διάβασα τις προειδοποιήσεις της διαδρομής": "I read the route warnings",
    "μίλησα με το πλήρωμα και είναι έτοιμο": "I spoke to the crew and they are ready",
    "Η υπόθεση δεν έχει θέση στον χάρτη: καμία μονάδα δεν μπορεί να σχεδιαστεί.":
        "The case has no location on the map: no unit can be planned.",
    "Η απόφαση της υπόθεσης δεν είναι αποστολή μονάδας.": "The case's decision is not to send a unit.",
    "Η υπόθεση είναι κλειστή ή συγχωνευμένη.": "The case is closed or merged.",
    "Η αποστολή έχει ήδη τελειώσει.": "The mission is already over.",
    # routing
    "Το σημείο απέχει πολύ από το οδικό δίκτυο.": "The point is far from the road network.",
    "Κανένας δρόμος δεν φτάνει εκεί χωρίς να περάσει από δρόμο που καίγεται τώρα.":
        "No road gets there without passing a road that is burning now.",
    "Το μέτωπο έχει ήδη περάσει από αυτό το σημείο.": "The front has already passed this point.",
    # warnings (112)
    "Δεν υπάρχει οδικό δίκτυο: δεν μπορεί να προταθεί διαδρομή.": "There is no road network: no route can be proposed.",
    "Δεν υπάρχουν δεδομένα ακτής: δεν προτείνεται μετάβαση στη θάλασσα.":
        "There is no shore data: going to the sea is not proposed.",
    "Δεν βρέθηκε ακτή με πρόσβαση στο νερό (μόνο γκρεμοί ή ανεξέλεγκτη ακτή).":
        "No shore with access to the water was found (only cliffs or unchecked shore).",
    "Η ακτή με πρόσβαση δεν έχει όνομα και κανένας δρόμος δεν καταλήγει εκεί: δεν μπορεί να περιγραφεί σε μήνυμα, "
    "οπότε δεν προτείνεται.": "The shore with access has no name and no road ends there: it cannot be described in "
                              "a message, so it is not proposed.",
    "Δεν υπάρχει οικισμός κοντά σε αυτό το σημείο.": "There is no settlement near this point.",
    "Η προειδοποίηση γράφεται ακόμα.": "The warning is still being written.",
    "Αλλάζει μόνο κείμενο προειδοποίησης που δεν έχει σταλεί.": "Only the text of an unsent warning can change.",
    "Στέλνεται μόνο προτεινόμενη προειδοποίηση.": "Only a proposed warning can be sent.",
    "Η προειδοποίηση δεν είναι ανοιχτή.": "The warning is not open.",
    # queue order (cases/service.py)
    "Ανοιχτό με προθεσμία: η πρόταση μετακινεί ανθρώπους ή στέλνει κάποιον.":
        "Open with a deadline: the proposal moves people or sends someone.",
    "Ανοιχτό, αλλά η πρόταση δεν μετακινεί ανθρώπους ούτε στέλνει κάποιον: μετά από όσα έχουν προθεσμία.":
        "Open, but the proposal neither moves people nor sends anyone: after those with a deadline.",
    "Δεν υπάρχει εκτίμηση για το πότε φτάνει το μέτωπο εκεί.": "There is no estimate of when the front gets there.",
    "Με ίση προθεσμία: πρώτα η υψηλότερη προτεραιότητα της πρότασης, μετά το παλαιότερο μήνυμα.":
        "With equal deadlines: the proposal's higher priority first, then the older message.",
    "Διεκπεραιώθηκε: κάτω από τα ανοιχτά.": "Handled: below the open cases.",
    "Το περιστατικό άλλαξε από τότε που το άνοιξες (νέο μήνυμα, απάντηση ή αναφορά). Δες τι άλλαξε και αποφάσισε "
    "ξανά.": "The case changed since you opened it (a new message, an answer or a report). See what changed and "
             "decide again.",
    # questions to the sender
    "Είστε ακόμα στο ίδιο σημείο; Πόσοι είστε τώρα; Έχει αλλάξει κάτι γύρω σας (φωτιά, καπνός, δρόμος);":
        "Are you still in the same place? How many are you now? Has anything changed around you (fire, smoke, road)?",
    "Σε ποιο σημείο της ακτής είστε; Υπάρχει κάτι γνωστό κοντά;":
        "Where on the shore are you? Is there anything well known nearby?",
    "Πού ακριβώς βρίσκεστε; Οδός και περιοχή.": "Where exactly are you? Street and area.",
    "Πού ακριβώς βρίσκεστε (οδός και περιοχή) και πόσα άτομα είστε;":
        "Where exactly are you (street and area) and how many are you?",
    "Βλέπετε φλόγες τώρα; Πόσο μακριά από εσάς, και προς ποια κατεύθυνση;":
        "Do you see flames now? How far from you, and in which direction?",
    "Κοιτάξτε γύρω σας τώρα: βλέπετε καπνό ή φλόγες; Από ποια μεριά;":
        "Look around you now: do you see smoke or flames? From which side?",
    "Πού ακριβώς είστε στον δρόμο; Βλέπετε καπνό ή καμένα από κάποια μεριά;":
        "Where exactly on the street are you? Do you see smoke or burnt ground on any side?",
    "Πόσοι ακριβώς είστε μαζί τώρα;": "Exactly how many of you are together now?",
    "Πού ακριβώς βρίσκεστε τώρα; Μετακινηθήκατε;": "Where exactly are you now? Did you move?",
    "Είστε όλοι ασφαλείς τώρα, ή μένει κάποιος πίσω;": "Are you all safe now, or is someone left behind?",
    "Ποιος είναι ακόμα εκεί, και πού ακριβώς;": "Who is still there, and where exactly?",
    "Είναι όλοι μαζί σας; Έφυγε κάποιος με άλλο τρόπο;": "Is everyone with you? Did anyone leave another way?",
    # contradictions
    "Ο αποστολέας λέει ότι βλέπει φωτιά κοντά, ενώ το μοντέλο φωτιάς δεν τη δίνει εκεί τόσο νωρίς. Η απόφαση ήδη "
    "εμπιστεύεται το μήνυμα· το μοντέλο είναι ανασύνθεση.": "The sender says they see fire nearby, while the fire model "
    "does not put it there so early. The decision already trusts the message; the model is a reconstruction.",
    "Ο αποστολέας λέει ότι η φωτιά είναι μακριά, ενώ το μοντέλο τη δίνει εκεί ή πολύ κοντά. Αν το μοντέλο έχει "
    "δίκιο, ο κίνδυνος υποτιμάται.": "The sender says the fire is far, while the model puts it there or very close. "
                                    "If the model is right, the danger is underestimated.",
    "Ο αποστολέας λέει ότι η φωτιά είναι μακριά· το μοντέλο φέρνει το μέτωπο μόνο στην άκρη του δρόμου ή της "
    "περιοχής. Μπορεί να ισχύουν και τα δύο: η θέση του αποστολέα κρίνει.": "The sender says the fire is far; the "
    "model brings the front only to the edge of the street or area. Both may be true: the sender's position decides.",
    # supervisor
    "Άνοιγμα περιστατικού": "Open the case", "Ελήφθη· επικοινωνία με πλήρωμα ή αποστολέα":
        "Seen; contact the crew or the sender", "Πρόταση 112 για την περιοχή": "Propose a 112 warning for the area",
    "Άνοιγμα αιτημάτων Λιμενικού": "Open the Coast Guard requests", "χειριστής περιστατικών": "case operator",
    "χειριστής ασυρμάτου": "radio operator", "υπεύθυνος 112": "112 officer", "σύνδεσμος Λιμενικού": "Coast Guard liaison",
    # handover
    "Σύνολα": "Totals", "Επανακλήσεις χωρίς απάντηση": "Call-backs with no answer",
    "Ανοιχτά περιστατικά που κινούν ανθρώπους (πρώτα όσα έχουν το μέτωπο κοντά)":
        "Open cases that move people (those with the front close first)",
    "Μονάδες ξηράς εκτός βάσης (όπως ανέφεραν τα πληρώματα)": "Ground units away from base (as the crews reported)",
    "Προειδοποιήσεις 112": "112 warnings", "Ειδοποιήσεις του Επιβλέποντα χωρίς λύση": "Supervisor alerts not resolved",
    "Αναφορές πεδίου που θέλουν συνέχεια": "Field reports that need follow-up",
    "Χρόνος σεναρίου· ό,τι έγινε μετά από αυτή την ώρα δεν μετράει.":
        "Scenario time; anything after this time does not count.",
    # agents in the room (flow/room.py)
    "Επόπτης": "Supervisor", "Συντάκτης 112": "112 writer", "Κάρτα αποστολής": "Crew brief",
    "Αναφορά πληρώματος": "Crew report", "Παράδοση βάρδιας": "Shift handover", "Σύνδεσμος Λιμενικού": "Coast Guard liaison",
    "βλέπει όλη την ουρά και ειδοποιεί για ό,τι ξεχάστηκε ή καθυστερεί":
        "watches the whole queue and flags what was forgotten or is late",
    "γράφει το κείμενο της προειδοποίησης, ελληνικά και αγγλικά": "writes the warning text, in Greek and English",
    "γράφει τις οδηγίες για το πλήρωμα όταν στέλνεται μονάδα": "writes the crew's instructions when a unit is sent",
    "διαβάζει τι είπε το πλήρωμα από το πεδίο": "reads what the crew said from the field",
    "γράφει την περίληψη για την επόμενη βάρδια": "writes the summary for the next shift",
    "ομαδοποιεί τη θάλασσα ανά ακτή, γράφει και ιεραρχεί τα αιτήματα":
        "groups the sea by shore, writes and ranks the requests",
    "κράτησε το κείμενο του πράκτορα": "kept the agent's text", "ο κώδικας το απέρριψε· μπήκε το πρότυπο":
        "code rejected it; template used", "χωρίς μοντέλο (κανόνες)": "no model (rules)",
    "η κλήση απέτυχε· μπήκε το πρότυπο": "the call failed; template used", "καμία νέα ειδοποίηση": "no new alert",
    "περιμένει τον χειριστή": "waiting for the operator", "ενημέρωσε το περιστατικό": "updated the case",
    "κατάσταση της αίθουσας": "state of the room", "τίποτα που να ελέγχεται": "nothing to check",
    "μόνο ο σκελετός με τους αριθμούς": "only the skeleton with the numbers", "στάλθηκε": "sent",
    "απορρίφθηκε": "dismissed", "έληξε χωρίς ενέργεια": "expired with no action", "αντικαταστάθηκε": "replaced",
    # agents for a message (flow nodes)
    "Πολίτης": "Citizen", "Πρόσληψη": "Reader", "Κριτής": "Critic", "Θέση": "Location", "Εργαλεία": "Tools",
    "Κίνδυνος": "Hazard", "Αξιοπιστία": "Credibility", "Ανάγκες": "Needs", "Διαιτητής": "Arbiter",
    "Απόφαση (κώδικας)": "Decision (code)", "Διπλά": "Duplicates", "Επανάκληση": "Call-back", "◆ Χειριστής": "◆ Operator",
    "Πλήρωμα": "Crew", "Λιμενικό": "Coast Guard",
    "καμία ανάγκη": "no need", "κανένα διπλό": "no duplicate", "ερωτήσεις": "questions",
    # scenario
    "Μάτι, 23 Ιουλίου 2018": "Mati, 23 July 2018", "χωρίς απόφαση": "no decision", "άγνωστα": "unknown",
    # call-back questions (agents/callback.py, code's fallback)
    "Σε ποια οδό και ποια περιοχή βρίσκεστε ακριβώς;": "Which street and which area are you in exactly?",
    "Σε ποια περιοχή είναι αυτή η οδός; Μάτι, Νέος Βουτζάς ή αλλού;":
        "Which area is this street in: Mati, Neos Voutzas or another?",
    "Σε ποια παραλία ή ποιο σημείο της ακτής είστε; Τι βλέπετε κοντά σας;":
        "Which beach or point of the shore are you at? What can you see near you?",
    "Πόσα άτομα είστε μαζί;": "How many people are with you?",
    "Μπορούν όλοι να περπατήσουν; Έχετε αυτοκίνητο;": "Can everyone walk? Do you have a car?",
    # supervisor alert kinds (api/state.py)
    "Μέτωπο κοντά, χωρίς απόφαση": "Front close, no decision", "Εγκρίθηκε, δεν στάλθηκε": "Approved, not sent",
    "Διαθέσιμες μονάδες, περιστατικά σε αναμονή": "Units available, cases waiting",
    "Μονάδα χωρίς αναφορά": "Unit with no report", "Μονάδα επί τόπου χωρίς αποτέλεσμα": "Unit on scene, no result",
    "Βλάβη μονάδας": "Unit breakdown", "Χωρίς νέα από ανθρώπους σε αναμονή": "No news from people waiting",
    "Συγκέντρωση περιστατικών": "Cluster of cases", "Θάλασσα χωρίς αίτημα στο Λιμενικό": "Sea with no Coast Guard request",
    # people at a place (handover)
    "χωρίς θέση": "no location",
}
EXACT.update({k: v for k, v in UNIT_STATE.items()})
EXACT.update({k: v for k, v in DECISION.items() if k not in EXACT})

# message flow (flow/service.py)
KIND_FLOW = {"ζητά βοήθεια": "asks for help", "είναι ασφαλείς": "they are safe", "αγνοούμενος": "missing person",
             "πληροφορία": "information", "ανεπιβεβαίωτο": "unconfirmed", "ερώτηση": "question",
             "εντολή προς το σύστημα": "orders to the system", "ασαφές": "unclear"}
HAZARD_FLOW = {"φλόγες": "flames", "καπνός": "smoke", "φωτιά κοντά": "fire nearby", "καίγεται κτίριο": "building on fire",
               "κλειστός δρόμος": "road blocked", "κίνηση": "traffic", "άλλο": "other"}
LOC_FLOW = {"σημείο": "point", "περίπου": "approximate", "αμφίσημη": "ambiguous", "δεν βρέθηκε": "not found",
            "εκτός περιοχής": "outside the area"}
LOC_OUTCOME = {"βρήκε": "found", "επιβεβαίωσε": "confirmed", "βρήκε τον δρόμο": "found the street",
               "διαφωνεί, μένει ο κώδικας": "disagrees, code's stands", "μάντεψε, ρωτάμε τον αποστολέα":
               "guessed, we ask the sender", "δεν ξεχώρισε": "could not tell", "απορρίφθηκε": "rejected",
               "απέτυχε": "failed", "δεκτή": "accepted", "κράτησε τον κώδικα": "kept code's"}
HUMAN_FLOW = {"καταχώριση μηνύματος": "message entered", "έγκριση": "approved", "αλλαγή απόφασης": "decision changed",
              "κλείσιμο": "closed", "επαναφορά": "reopened", "συγχώνευση": "merged", "όχι διπλό": "not a duplicate",
              "απάντηση στην επανάκληση": "answer to the call-back", "κρίση ασυμφωνίας": "contradiction judged",
              "αποστολή μονάδας": "unit sent", "ακύρωση": "cancelled", "αναφορά πληρώματος": "crew report",
              "ερωτήσεις στον αποστολέα": "questions to the sender", "αποστολή 112": "112 sent",
              "απόρριψη 112": "112 dismissed", "πρόταση 112": "112 proposed", "ειδοποίηση επόπτη": "supervisor alert",
              "απόφαση σε ειδοποίηση": "decision on an alert", "αίτημα στο Λιμενικό": "Coast Guard request",
              "ερώτηση ασυμφωνίας": "contradiction question"}
EXACT.update(HUMAN_FLOW)

Sub = Callable[[re.Match[str]], str]
RULES: list[tuple[re.Pattern[str], Sub]] = []


def rule(pattern: str) -> Callable[[Sub], Sub]:
    def add(fn: Sub) -> Sub:
        RULES.append((re.compile(pattern, re.S), fn))
        return fn
    return add


def _list(s: str, words: dict[str, str]) -> str:
    return ", ".join(words.get(w.strip(), w.strip()) for w in s.split(","))


def _people(n: str) -> str:
    return f"{n} person" if n == "1" else f"{n} people"


def _near(m: re.Match[str], g: str = "near") -> str:
    v = m.group(g)
    return f" at a point about {v} m from the given location" if v else ""


@rule(r"(?P<n>\d+) άτομα\.")
def _(m): return _people(m["n"]) + "."


@rule(r"Ευάλωτοι: (?P<l>.+)\.")
def _(m): return f"Vulnerable: {_list(m['l'], VULN)}."


@rule(r"Άτομα: (?P<n>\d+), μετρημένα από «(?P<q>.+)» μαζί με τον αποστολέα· επιβεβαίωσε τον αριθμό\.")
def _(m): return f"People: {m['n']}, counted from «{m['q']}» plus the sender; confirm the number."


@rule(r"Κατά τη λήψη του μηνύματος το μέτωπο απείχε περίπου (?P<m>\d+)′(?: σε σημείο περίπου (?P<near>\d+) m από "
      r"τη θέση που δόθηκε)?\.")
def _(m): return f"When the message came in, the front was about {m['m']}′ away{_near(m)}."


@rule(r"Κατά τη λήψη του μηνύματος το μέτωπο είχε ήδη φτάσει(?: σε σημείο περίπου (?P<near>\d+) m από τη θέση που "
      r"δόθηκε)? \(πριν από (?P<m>\d+)′\)\.")
def _(m): return f"When the message came in, the front had already arrived{_near(m)} ({m['m']}′ before)."


@rule(r"Η τοποθεσία \((?P<l>.+)\) είναι εκτός περιοχής σεναρίου\.")
def _(m): return f"The location ({m['l']}) is outside the scenario area."


@rule(r"Δεν μπορούν να φύγουν μόνοι τους \(«(?P<q>.+)»\)\.")
def _(m): return f"They cannot leave on their own («{m['q']}»)."


@rule(r"(?P<who>Διαιτητής|Αξιοπιστία): (?P<rest>.+)")
def _(m): return {"Διαιτητής": "Arbiter", "Αξιοπιστία": "Credibility"}[m["who"]] + ": " + tr(m["rest"])


@rule(r"Ο κριτής κινδύνου άλλαξε την πρόταση από «(?P<d>[A-Z_]+)»: (?P<rest>.+)")
def _(m): return f"The hazard critic changed the proposal from «{m['d']}»: " + "; ".join(
    tr(x) for x in m["rest"].split("; "))


@rule(r"Αναφορά πεδίου \((?P<u>[^)]+)\): (?P<rest>.+)")
def _(m): return f"Field report ({m['u']}): {tr(m['rest'])}"


@rule(r"Ο έλεγχος διπλοεγγραφής απέτυχε: (?P<e>.+)")
def _(m): return f"The duplicate check failed: {m['e']}"


# hazard facts
@rule(r"Λένε ότι δεν μπορούν να φύγουν \(«(?P<q>.+)»\)\.")
def _(m): return f"They say they cannot leave («{m['q']}»)."


@rule(r"Το μέτωπο έχει ήδη περάσει \((?P<m>\d+)′ πριν\): κίνδυνος από καπνό και αναζωπυρώσεις, όχι λόγος να "
      r"σταματήσει η βοήθεια\.")
def _(m): return (f"The front has already passed ({m['m']}′ ago): danger from smoke and flare-ups, no reason to stop "
                  "the help.")


@rule(r"Το μέτωπο φτάνει σε περίπου (?P<m>\d+)′\.")
def _(m): return f"The front arrives in about {m['m']}′."


@rule(r"Είναι κλεισμένοι μέσα \(«(?P<q>.*)»\)\.")
def _(m): return f"They are shut inside («{m['q']}»)."


@rule(r"Υπάρχει διαδρομή προς ασφαλή ζώνη με ελάχιστο περιθώριο (?P<m>\d+)′ από το μέτωπο\.(?: Περνά από αδιέξοδο "
      r"\((?P<d>\d+) τμ\.\)\.)?")
def _(m): return (f"There is a route to a safe zone with a minimum margin of {m['m']}′ from the front."
                  + (f" It passes a dead end ({m['d']} segments)." if m["d"] else ""))


@rule(r"Καμία διαδρομή με όχημα δεν φτάνει σε ασφαλή ζώνη πριν από το μέτωπο \((?P<s>[^)]+)\)\.")
def _(m): return f"No vehicle route reaches a safe zone before the front ({m['s']})."


@rule(r"Καμία διαδρομή με όχημα δεν φτάνει σε ασφαλή ζώνη πριν από το μέτωπο \(περιθώριο (?P<m>\d+) λεπτά\)\. "
      r"Χρειάζεται εναλλακτική: καταφύγιο ή παραλία με τα πόδια\.")
def _(m): return (f"No vehicle route reaches a safe zone before the front (margin {m['m']} minutes). An alternative "
                  "is needed: a shelter or a beach on foot.")


# credibility signals
@rule(r"(?P<n>\d+) άτομα σε (?P<w>σπίτι|όχημα)\.")
def _(m): return f"{_people(m['n'])} in a {'house' if m['w'] == 'σπίτι' else 'vehicle'}."


@rule(r"Αναφέρει φωτιά εκεί όπου η ανασύνθεση τη φέρνει σε (?P<m>-?\d+)′\.")
def _(m): return f"It reports fire where the reconstruction brings it in {m['m']}′."


# missions and routes
@rule(r"ιατρική ανάγκη: (?P<l>.+)")
def _(m): return "medical need: " + ("they ask for medical help" if m["l"] == "ζητούν ιατρική βοήθεια"
                                     else m["l"].replace("_", " "))


@rule(r"Η μονάδα ξηράς θέλει (?P<m>\d+)′ και η θέση είναι κοντά στην ακτή: εξέτασε και σκάφος\.")
def _(m): return f"The ground unit needs {m['m']}′ and the location is near the shore: consider a boat too."


@rule(r"Περίπου (?P<km>[\d,.]+) km της διαδρομής είναι σε δρόμους όπου το μέτωπο έχει ήδη περάσει: καπνός, "
      r"συντρίμμια, καμένα οχήματα\.")
def _(m): return (f"About {m['km'].replace(',', '.')} km of the route is on roads the front has already passed: "
                  "smoke, debris, burnt vehicles.")


@rule(r"Η διαδρομή μπαίνει σε αδιέξοδο \((?P<d>\d+) τμ\. δρόμου\): έλεγξε ότι δεν εγκλωβίζει\.")
def _(m): return f"The route enters a dead end ({m['d']} road segments): check that it does not trap them."


@rule(r"Το σημείο εκκίνησης απέχει (?P<m>\d+) m από τον πλησιέστερο δρόμο\.")
def _(m): return f"The starting point is {m['m']} m from the nearest road."


@rule(r"(?P<a>Κανένα|Το πρώτο) (?P<k>πυροσβεστικό όχημα|ασθενοφόρο) (?:δεν φτάνει|φτάνει (?P<late>\d+)′ αργότερα)· "
      r"προτείνεται (?P<o>πυροσβεστικό όχημα|ασθενοφόρο)\.")
def _(m): return ((f"No {KIND[m['k']]} gets there" if m["a"] == "Κανένα" else
                   f"The first {KIND[m['k']]} arrives {m['late']}′ later") + f"; a {KIND[m['o']]} is proposed.")


@rule(r"Πριν από την αποστολή: (?P<l>.+)")
def _(m): return "Before sending: " + "; ".join(tr(x) for x in m["l"].split("; "))


@rule(r"Η μονάδα (?P<u>\S+) δεν έχει αναφέρει ότι είναι διαθέσιμη\.")
def _(m): return f"Unit {m['u']} has not reported that it is available."


@rule(r"Η πρόταση είναι η (?P<u>\S+)· για άλλη μονάδα γράψε γιατί\.")
def _(m): return f"The proposal is {m['u']}; for another unit, write why."


# 112 warnings
@rule(r"Και τα (?P<n>\d+) δείγματα κτιρίων βγαίνουν με όχημα, με περιθώριο (?P<m>\d+)′ από το μέτωπο\.")
def _(m): return f"All {m['n']} sample buildings get out by vehicle, with a margin of {m['m']}′ from the front."


@rule(r"Μόνο (?P<a>\d+) από (?P<b>\d+) δείγματα κτιρίων έχουν ασφαλή διαδρομή με όχημα: δεν προτείνεται μετακίνηση "
      r"με αυτοκίνητο\.")
def _(m): return (f"Only {m['a']} of {m['b']} sample buildings have a safe route by vehicle: leaving by car is not "
                  "proposed.")


@rule(r"Για (?P<a>\d+) από (?P<b>\d+) δείγματα η πιο κοντινή ακτή είναι γκρεμός· μετριέται η απόσταση ως σημείο με "
      r"πρόσβαση στο νερό\.")
def _(m): return (f"For {m['a']} of {m['b']} samples the nearest shore is a cliff; the distance is measured to a point "
                  "with access to the water.")


@rule(r"Η πλησιέστερη ακτή με πρόσβαση στο νερό απέχει περίπου (?P<a>\d+) m \(έως (?P<b>\d+) m\): πολύ μακριά με τα "
      r"πόδια\.")
def _(m): return f"The nearest shore with access to the water is about {m['a']} m away (up to {m['b']} m): too far on foot."


@rule(r"Προς: (?:παραλία στο τέλος της οδού (?P<road>.+?)|παραλία (?P<name>.+?)), περίπου (?P<a>\d+) m \(διάμεση "
      r"απόσταση, έως (?P<b>\d+) m\)\.")
def _(m): return ("To: " + (f"the beach at the end of {m['road']} street" if m["road"] else f"{m['name']} beach")
                  + f", about {m['a']} m (median distance, up to {m['b']} m).")


@rule(r"Το μέτωπο δεν φτάνει σε κτίρια της περιοχής (?P<s>.+) την επόμενη ώρα\.")
def _(m): return f"The front does not reach buildings in {m['s']} in the next hour."


# queue order
@rule(r"Το μέτωπο φτάνει σε (?P<m>\d+)′\.")
def _(m): return f"The front arrives in {m['m']}′."


@rule(r"Το μέτωπο έφτασε πριν από (?P<m>\d+)′\.")
def _(m): return f"The front arrived {m['m']}′ ago."


@rule(r"Είναι νωρίτερα από το μέτωπο \((?P<m>\d+)′\)\.")
def _(m): return f"It is earlier than the front ({m['m']}′)."


@rule(r"Συγχωνεύτηκε στο (?P<c>\S+): στο τέλος της λίστας\.")
def _(m): return f"Merged into {m['c']}: at the end of the list."


@rule(r"Η προθεσμία είναι η νωρίτερη μαζί με τα συγχωνευμένα \((?P<l>.+)\)\.")
def _(m): return f"The deadline is the earliest together with the merged ones ({m['l']})."


# location questions
@rule(r"Ποια είναι η ακριβής διεύθυνση στην περιοχή (?P<a>.+);")
def _(m): return f"What is the exact address in {place(m['a'])}?"


@rule(r"Είστε στην οδό (?P<a>.+) ή στην οδό (?P<b>.+); Τι βλέπετε γύρω σας \(πινακίδα, μαγαζί, εκκλησία, θάλασσα\);")
def _(m): return f"Are you on {m['a']} or on {m['b']}? What do you see around you (a sign, a shop, a church, the sea)?"


@rule(r"Επιβεβαίωση: η οδός (?P<n>.+) στην περιοχή (?P<a>.+);")
def _(m): return f"To confirm: {m['n']} street in {m['a']}?"


@rule(r"Σε ποια περιοχή είναι η οδός (?P<n>.+); \((?P<l>.+)\)")
def _(m): return f"Which area is {m['n']} street in? ({m['l']})"


@rule(r"ακτή, (?P<a>.+)")
def _(m): return f"shore, {m['a']}"


# contradictions
@rule(r"Το ανασυνθεμένο μέτωπο (?P<w>.+?) από εκεί\.")
def _(m): return f"The reconstructed front {_when(m['w'])} from there."


@rule(r"Το ανασυνθεμένο μέτωπο (?P<w>φτάνει εκεί .+|δεν φτάνει σε αυτό το σημείο)\.")
def _(m): return f"The reconstructed front {_when(m['w'])}."


@rule(r"Το ανασυνθεμένο μέτωπο (?P<w>.+?) σε σημείο περίπου (?P<d>.+?) από τη θέση που δόθηκε· την ίδια τη θέση δεν "
      r"τη φτάνει\.")
def _(m): return (f"The reconstructed front {_when(m['w'])} at a point about {m['d']} from the given location; it does "
                  "not reach the location itself.")


def _when(w: str) -> str:
    for pat, out in ((r"έχει ήδη περάσει \((?P<m>\d+′) πριν από το μήνυμα\)", "had already passed ({m} before the message)"),
                     (r"φτάνει σε (?P<m>\d+′)", "arrives in {m}"),
                     (r"φτάνει εκεί (?P<m>\d+′) μετά το μήνυμα", "gets there {m} after the message"),
                     (r"δεν φτάνει σε αυτό το σημείο", "does not reach this point")):
        x = re.fullmatch(pat, w)
        if x:
            return out.format(**x.groupdict())
    return w


@rule(r"Τα μηνύματα για τους ίδιους ανθρώπους δίνουν διαφορετικό αριθμό: (?P<a>\d+) και (?P<b>\d+)\. Για τον "
      r"σχεδιασμό μετράει ο μεγαλύτερος \((?P<c>\d+)\) μέχρι να επιβεβαιωθεί\.")
def _(m): return (f"The messages about the same people give different numbers: {m['a']} and {m['b']}. Planning counts "
                  f"the larger ({m['c']}) until it is confirmed.")


@rule(r"Ένα μήνυμα ζητά βοήθεια και ένα λέει ότι είναι ασφαλείς\. Νεότερο είναι το (?P<c>\S+)\.")
def _(m): return f"One message asks for help and one says they are safe. The newer one is {m['c']}."


# supervisor
@rule(r"(?P<n>\d+) (?:περιστατικό που κινεί|περιστατικά που κινούν) ανθρώπους είναι χωρίς απόφαση χειριστή με το "
      r"μέτωπο σε (?P<m>\d+)′ ή λιγότερο(?: \((?P<p>\d+) με το μέτωπο ήδη εκεί\))?\. (?P<rest>.+)")
def _(m): return (f"{m['n']} case(s) that move people have no operator decision with the front {m['m']}′ away or less"
                  + (f" ({m['p']} with the front already there)" if m["p"] else "") + ". "
                  + m["rest"].replace("έχει φτάσει", "arrived"))


@rule(r"(?P<n>\d+) (?:περιστατικό|περιστατικά) που περιμένουν βοήθεια χωρίς κανένα νέο εδώ και 20′ ή περισσότερο "
      r"\(το μεγαλύτερο (?P<m>\d+)′\)\. Ερώτηση: «Είστε ακόμα στο ίδιο σημείο;»\. (?P<rest>.+)")
def _(m): return (f"{m['n']} case(s) waiting for help with no news for 20′ or more (the longest {m['m']}′). "
                  f"Question: «Are you still in the same place?». {m['rest']}")


@rule(r"Μήνυμα από τη θάλασσα ή την ακτή περιμένει (?P<m>\d+)′ \(από τις (?P<t>[\d:]+)\) χωρίς αίτημα στο Λιμενικό· "
      r"ανοιχτά περιστατικά θάλασσας: (?P<n>\d+)\.")
def _(m): return (f"A message from the sea or the shore has waited {m['m']}′ (since {m['t']}) with no request to the "
                  f"Coast Guard; open sea cases: {m['n']}.")


@rule(r"(?P<n>\d+) (?:περιστατικό εγκρίθηκε|περιστατικά εγκρίθηκαν) για αποστολή μονάδας αλλά δεν έχει σταλεί μονάδα "
      r"\(μεγαλύτερη αναμονή (?P<m>\d+)′\)\. (?P<rest>.+)")
def _(m): return (f"{m['n']} case(s) approved for a unit but no unit has been sent (longest wait {m['m']}′). "
                  f"{m['rest']}")


@rule(r"(?P<a>\d+) (?:μονάδα ξηράς είναι διαθέσιμη|μονάδες ξηράς είναι διαθέσιμες) ενώ (?P<b>\d+) (?:περιστατικό "
      r"«αποστολή μονάδας» περιμένει|περιστατικά «αποστολή μονάδας» περιμένουν) χωρίς μονάδα \(μεγαλύτερη αναμονή "
      r"(?P<m>\d+)′\)\. (?P<rest>.+)")
def _(m): return (f"{m['a']} ground unit(s) available while {m['b']} «send a unit» case(s) wait with no unit (longest "
                  f"wait {m['m']}′). {m['rest']}")


@rule(r"(?P<n>\d+) (?:επανάκληση|επανακλήσεις) χωρίς απάντηση για (?P<a>\d+)′ ή περισσότερο \(μεγαλύτερη αναμονή "
      r"(?P<m>\d+)′\)\. (?P<rest>.+)")
def _(m): return f"{m['n']} call-back(s) with no answer for {m['a']}′ or more (longest wait {m['m']}′). {m['rest']}"


@rule(r"Η μονάδα (?P<u>\S+) \(περιστατικό (?P<c>\S+)\) δεν έχει αναφέρει άφιξη (?P<m>\d+)′ μετά την εκτιμώμενη ώρα "
      r"άφιξης (?P<t>[\d:]+)\. Τελευταία κατάσταση: (?P<s>.+)\.")
def _(m): return (f"Unit {m['u']} (case {m['c']}) has not reported arriving {m['m']}′ after the expected arrival "
                  f"{m['t']}. Last state: {UNIT_STATE.get(m['s'], m['s'])}.")


@rule(r"Η μονάδα (?P<u>\S+) είναι επί τόπου στο (?P<c>\S+) εδώ και (?P<m>\d+)′ \(σχέδιο (?P<p>\d+)′\) χωρίς αναφορά "
      r"για το τι βρήκε\.")
def _(m): return (f"Unit {m['u']} has been on scene at {m['c']} for {m['m']}′ (plan {m['p']}′) with no report of "
                  "what it found.")


@rule(r"Η μονάδα (?P<u>\S+) ανέφερε βλάβη στην αποστολή για το (?P<c>\S+) και καμία άλλη μονάδα δεν πηγαίνει εκεί\.")
def _(m): return f"Unit {m['u']} reported a breakdown on the mission to {m['c']} and no other unit is going there."


# handover
@rule(r"Μηνύματα ως τις (?P<t>[\d:—]+): (?P<n>\d+), με απόφαση χειριστή (?P<d>\d+)\.")
def _(m): return f"Messages up to {m['t']}: {m['n']}, with an operator decision {m['d']}."


@rule(r"Περιστατικά που έκλεισαν με αναφορά πεδίου: (?P<a>\d+)· άτομα που ανέφεραν ως διασωθέντα οι μονάδες ξηράς: "
      r"(?P<b>\d+)\.")
def _(m): return f"Cases closed by a field report: {m['a']}; people the ground units reported rescued: {m['b']}."


@rule(r"(?P<id>[mw]\d+) (?P<l>.+?): (?P<d>[^,]+), (?P<f>το μέτωπο έχει φτάσει|μέτωπο σε \d+′|μέτωπο: —), άτομα "
      r"(?P<p>[^,]+), (?P<s>.+)\.")
def _(m):
    f = {"το μέτωπο έχει φτάσει": "the front has arrived", "μέτωπο: —": "front: —"}.get(
        m["f"], m["f"].replace("μέτωπο σε", "front in"))
    label = "no location" if m["l"] == "χωρίς θέση" else place(tr(m["l"]))
    return (f"{m['id']} {label}: {DECISION.get(m['d'], m['d'])}, {f}, people {tr(m['p'])}, {tr(m['s'])}.")


@rule(r"(?P<id>[mw]\d+): ερωτήσεις στάλθηκαν (?P<t>[\d:—]+), χωρίς απάντηση (?P<m>\d+)′\.")
def _(m): return f"{m['id']}: questions sent {m['t']}, no answer for {m['m']}′."


@rule(r"112 για (?P<s>.+) περιμένει απόφαση χειριστή \((?P<a>.+)\)\.")
def _(m): return f"112 for {place(m['s'])} waits for an operator decision ({DECISION.get(m['a'], m['a'])})."


@rule(r"112 στάλθηκε (?P<t>[\d:—]+) για (?P<s>.+): (?P<a>.+)\.")
def _(m): return f"112 sent {m['t']} for {place(m['s'])}: {DECISION.get(m['a'], m['a'])}."


@rule(r"(?P<id>[mw]\d+): (?:μένει 1 άτομο|μένουν (?P<n>\d+) άτομα) \(αναφορά πεδίου\)\.")
def _(m): return f"{m['id']}: {_people(m['n'] or '1')} still there (field report)."


# room agents
@rule(r"(?P<n>\d+) ευρήματα στην ουρά")
def _(m): return f"{m['n']} findings in the queue"


@rule(r"(?P<n>\d+) ειδοποιήσεις(?:: (?P<w>.+))?")
def _(m): return f"{m['n']} alerts" + (f": {tr(m['w'])}" if m["w"] else "")


@rule(r"ο χειριστής αποφάσισε για (?P<a>\d+) από (?P<b>\d+)")
def _(m): return f"the operator decided on {m['a']} of {m['b']}"


@rule(r"αποστολή από (?P<w>\S+)(?P<r> \(ξαναγράφτηκε μία φορά\))?")
def _(m): return f"sent by {m['w']}" + (" (rewritten once)" if m["r"] else "")


@rule(r"(?P<who>στάλθηκε από|από) (?P<w>[\w.-]+)")
def _(m): return ("sent by " if m["who"] == "στάλθηκε από" else "by ") + m["w"]


@rule(r"προειδοποίηση για (?P<s>.+)")
def _(m): return f"warning for {m['s']}"


@rule(r"(?P<n>\d+) ακτές, (?P<k>\d+) μηνύματα · (?P<c>\d+) κλήσεις εργαλείων")
def _(m): return f"{m['n']} shores, {m['k']} messages · {m['c']} tool calls"


# message flow (flow/service.py)
@rule(r"πρόταση: (?P<d>[^·]+?)(?: · μέτωπο (?P<m>[+-]\d+)′)?")
def _(m): return f"proposal: {DECISION.get(m['d'], m['d'])}" + (f" · front {m['m']}′" if m["m"] else "")


@rule(r"ίδιο με (?P<c>\S+)")
def _(m): return f"same as {m['c']}"


@rule(r"απέτυχε: (?P<e>.+)")
def _(m): return "failed: " + ("no answer" if m["e"] == "χωρίς απάντηση" else m["e"])


@rule(r"κανόνας (?P<r>\S+)(?P<c> · άλλαξε την απόφαση)?")
def _(m): return f"rule {m['r']}" + (" · changed the decision" if m["c"] else "")


@rule(r"ενστάσεις (?P<n>\d+)(?P<r> · διόρθωσε την ανάγνωση)?")
def _(m): return f"objections {m['n']}" + (" · corrected the reading" if m["r"] else "")


@rule(r"προειδοποιήσεις (?P<n>\d+)(?: · άλλαξε: (?P<c>.+))?")
def _(m): return f"cautions {m['n']}" + (f" · changed: {m['c']}" if m["c"] else "")


@rule(r"(?P<k>[^·]+?) · άτομα (?P<n>\d+|—)(?: · (?P<h>[^·]+?))?(?P<i> · εντολή προς το σύστημα)?")
def _(m):
    if m["k"] not in KIND_FLOW:
        raise KeyError(m["k"])
    return (f"{KIND_FLOW[m['k']]} · people {m['n']}" + (f" · {_list(m['h'], HAZARD_FLOW)}" if m["h"] else "")
            + (" · orders to the system" if m["i"] else ""))


@rule(r"(?:από κώδικα: )?(?P<l>.+?) \((?P<s>σημείο|περίπου|αμφίσημη|δεν βρέθηκε|εκτός περιοχής)\)"
      r"(?: · (?P<o>[^:]+?))?(?:: (?P<p>.+))?")
def _(m):
    head = ("from code: " if m.string.startswith("από κώδικα: ") else "") + f"{place(tr(m['l']))} ({LOC_FLOW[m['s']]})"
    return head + (f" · {LOC_OUTCOME.get(m['o'], m['o'])}" if m["o"] else "") + (f": {m['p']}" if m["p"] else "")


@rule(r"(?P<t>[^·]+?)(?P<a> · ασθενοφόρο)?")
def _(m):
    parts = [x.strip() for x in m["t"].split(",")]
    if not all(x in NEEDS for x in parts):
        raise KeyError(m["t"])
    return ", ".join(NEEDS[x] for x in parts) + (" · ambulance" if m["a"] else "")


@rule(r"(?P<t>.+?) \((?P<h>\d\d:\d\d)\): (?P<f>.+)")
def _(m):
    if m["t"] not in EXACT:
        raise KeyError(m["t"])
    return f"{EXACT[m['t']]} ({m['h']}): {tr(m['f'])}"


@rule(r"(?P<l>[^,]+(?:, [^,]+)*)")
def _(m):
    parts = [x.strip() for x in m["l"].split(",")]
    words = {**VULN, **NEEDS}
    if len(parts) < 1 or not all(x in words for x in parts):
        raise KeyError(m["l"])
    return ", ".join(words[x] for x in parts)


def tr(s: str) -> str:
    """The English for a sentence code wrote in Greek; anything else comes back as it was."""
    if not s:
        return s
    hit = EXACT.get(s)
    if hit is not None:
        return hit
    for pat, fn in RULES:
        m = pat.fullmatch(s)
        if m:
            try:
                return fn(m)
            except (KeyError, IndexError):
                return s
    return s


# Fields that are data, never translated: what people wrote, quotes, names of places and units.
DATA = {"text", "quote", "message", "quotes", "reply", "replies", "places", "name", "roads", "settlement",
        "destination", "refuge", "base", "unit", "id", "prefix", "area", "given", "wrote", "brief", "el_text",
        "answers", "from_centre", "to_sender", "sources"}
# ...except these, which are code's own words even though the key looks like data.
CODE_TEXT = {"text_el", "question_el", "question_fixed_el", "reason_el", "why_el", "summary_el", "fact_el",
             "action_el", "owner_el", "title_el", "label_el", "state_el", "does_el", "note_el"}


# Names of places: in English they are written in Latin letters (as the English 112 text does: Μάτι -> Mati).
PLACES = {"label", "where", "settlement", "area", "destination", "refuge", "roads", "base", "coast_name", "coast_road"}
NAME = re.compile(r"[Α-Ωα-ωΆ-Ώά-ώϊϋΐΰ0-9 ,.'’-]{2,60}")


def place(s: str) -> str:
    """A Greek place name, or 'street, area', in Latin letters; anything that is not a short name stays."""
    if not NAME.fullmatch(s) or len(s.split()) > 6 or not re.search(r"[Α-Ωα-ω]", s):
        return s
    from palladion.agents.alert_writer import latin
    s = s.replace("ϋ", "y").replace("ϊ", "i").replace("ΰ", "y").replace("ΐ", "i")   # Ταϋγέτου -> Taygetou
    return ", ".join(latin(part.strip()) for part in s.split(","))


def translate(obj: Any, key: str = "") -> Any:
    """Walk a JSON value and put code's Greek sentences into English, in place of the Greek."""
    if isinstance(obj, dict):
        return {k: (translate_place(v) if k in PLACES else v if (k in DATA and k not in CODE_TEXT)
                    else translate(v, k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [translate(v, key) for v in obj]
    if isinstance(obj, str):
        return tr(obj)
    return obj


def translate_place(v: Any) -> Any:
    if isinstance(v, str):
        t = tr(v)
        return place(t) if t == v else _latin_tail(t)
    if isinstance(v, list):
        return [translate_place(x) for x in v]
    return translate(v)


def _latin_tail(s: str) -> str:
    """'shore, Μάτι' after the rule -> 'shore, Mati'."""
    head, sep, tail = s.rpartition(", ")
    return f"{head}{sep}{place(tail)}" if sep else s
