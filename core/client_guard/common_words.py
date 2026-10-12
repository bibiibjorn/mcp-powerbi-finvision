"""Built-in common words: values that must only be masked when they are the WHOLE string.

A client family called "Test" or "Holding" must not turn every "Latest" / "holding" in a prompt
into a token, so these words (after normalisation) are matched whole-string only, whatever their
length. Frozen EN/NL/FR vocabulary of frequent finance / reporting terms.
"""

from __future__ import annotations

from pathlib import Path

_EN = """
test data total group holding holdings family services service analysis value values name names
client clients cash equity bank fund funds account accounts amount amounts balance balances budget
capital company companies customer customers date debit credit default description entity entities
expense expenses finance financial general income interest invoice invoices ledger management
market month number order orders other partner partners payment payments period portfolio price
product products profit project projects quantity rate rates report reports revenue sales
security securities status tax taxes trust type types unknown vendor vendors year years asset
assets liability liabilities summary detail details category categories currency reference
overview open closed active inactive current previous next last first all none yes true false
null blank empty actual forecast scenario version position positions transaction transactions
fee fees cost costs margin growth return returns risk risks limit limits target targets policy
contract contracts agreement owner owners member members office offices manager managers
director directors advisor advisors adviser private public limited international global
national european foundation association institute society partnership investment investments
real estate property properties loan loans mortgage dividend dividends bond bonds share shares
stock stocks option options deposit deposits transfer transfers income expenses result results
""".split()

_NL = """
familie families bedrijf bedrijven klant klanten leverancier leveranciers rekening rekeningen
totaal bedrag bedragen saldo saldi waarde waarden naam namen datum groep groepen diensten dienst
beheer kosten omzet winst verlies belasting belastingen factuur facturen betaling betalingen
periode beleggingen belegging fonds kapitaal vermogen jaar maand dag week overzicht rapport
rapporten eigenaar eigenaars partner vennootschap stichting vereniging boekhouding activa passiva
inkomsten uitgaven rente aandelen obligaties vastgoed lening leningen hypotheek dividend
verzekering verzekeringen overdracht storting opname huur onderhoud algemeen overig overige
onbekend leeg actief inactief gesloten open vorig volgend
""".split()

_FR = """
famille familles societe société sociétés compte comptes montant montants solde soldes valeur
valeurs nom noms groupe groupes gestion couts coûts chiffre bénéfice benefice perte taxe taxes
facture factures paiement paiements période periode fonds banque capital patrimoine année annee
mois jour semaine aperçu apercu rapport rapports propriétaire proprietaire associé associe
fondation association comptabilité comptabilite actifs passifs revenus dépenses depenses intérêt
interet actions obligations immobilier prêt pret prêts hypothèque hypotheque dividende assurance
assurances virement dépôt depot retrait loyer entretien général general divers inconnu vide
actif inactif fermé ferme ouvert précédent precedent suivant client clients fournisseur
fournisseurs
""".split()

# Software / reporting vocabulary that a frequency list of everyday language misses but that runs
# through every coding session (instructions, tool descriptions, code): a dimension value or GL
# account that happens to be one of these words must not turn it into a pseudonym.
_TECH = """
admin admins administrator editor editors include includes included including exclude excludes
excluded excluding post posts get put patch delete software hardware completed complete planning
plan plans query queries schema schemas table tables column columns measure measures visual
visuals page pages report reports filter filters slicer slicers model models dataset datasets
config configuration settings setting option options parameter parameters default defaults
server servers client clients session sessions token tokens endpoint endpoints request requests
response responses error errors warning warnings debug trace timing timings install update
upgrade deploy deployment commit commits branch branches merge pull push release version
versions build builds test tests testing review reviews workspace workspaces folder folders
file files path paths script scripts hook hooks plugin plugins tool tools agent agents task
tasks workflow workflows pipeline pipelines notebook notebooks lakehouse warehouse capacity
payable payables receivable receivables accrual accruals ageing aging overdue outstanding
invoice invoiced bucket buckets current share forecast budget actuals journal journals ledger
posting postings entry entries opening closing adjustment adjustments intercompany elimination
consolidation segment segments region regions division divisions department departments
contact contacts vendor vendors supplier suppliers customer customers employee employees
""".split()


def _frequency_words() -> list[str]:
    """The bundled EN/NL frequency list (``data/common_en_nl.txt``); empty when it is missing."""
    path = Path(__file__).with_name("data") / "common_en_nl.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [w.strip() for w in lines if w.strip() and not w.startswith("#")]


COMMON_WORDS: frozenset[str] = frozenset(w.casefold() for w in (*_EN, *_NL, *_FR, *_TECH))
"""Casefolded curated set, for every value (the engine normalises it again with its own rules)."""

EVERYDAY_WORDS: frozenset[str] = frozenset(w.casefold() for w in _frequency_words())
"""Casefolded everyday vocabulary (frequency list). Used for GENERIC values only (dimension values,
GL account names): it holds first names and surnames ("smith", "mark"), so a person or a company
called that must keep matching inside a sentence."""
