"""Harder synthetic cases (no real client data): names without a cue word, names that are ordinary
words, non-Flemish Belgian names, ALL CAPS, long mails, tables pasted as text, and look-alike
negatives (products, places, roles). Same item shape as ner_set_basic.py."""

ITEMS = [
    (
        "nl",
        "Yusuf Demir en Fatima El Amrani tekenen morgen de aandeelhoudersovereenkomst.",
        ["Yusuf Demir", "Fatima El Amrani"],
        [],
    ),
    ("nl", "VANHOUTTE NV heeft de huur van het magazijn opgezegd.", ["VANHOUTTE NV"], []),
    (
        "nl",
        "Groen Dak BV en Zwart Vastgoed moeten in hetzelfde rapport.",
        ["Groen Dak BV", "Zwart Vastgoed"],
        [],
    ),
    ("nl", "De Wit belde net terug, hij gaat akkoord met het voorstel.", ["De Wit"], []),
    (
        "nl",
        "Bespreking met Vermeulen en Goethals over de herstructurering.",
        ["Vermeulen", "Goethals"],
        [],
    ),
    (
        "nl",
        "Hoi, kan jij de Q3-cijfers van Brouwerij Het Anker Invest checken? Thx, Kris",
        ["Brouwerij Het Anker Invest", "Kris"],
        [],
    ),
    ("nl", "Bijlage: overzicht beleggingen Familie Coppens - versie 3.", ["Coppens"], []),
    (
        "nl",
        "Ter info: Marc en Ann Vandenbroucke-Segers verhuizen naar Knokke.",
        ["Marc", "Ann Vandenbroucke-Segers"],
        ["Knokke"],
    ),
    (
        "nl",
        "Het dossier Okafor wordt overgenomen door collega Liesbeth.",
        ["Okafor", "Liesbeth"],
        [],
    ),
    ("nl", "Totaal Activa en Totaal Passiva sluiten niet aan in de balans van dit jaar.", [], []),
    ("nl", "Gebruik DAX Studio en Tabular Editor om de query te analyseren.", [], []),
    (
        "nl",
        "Antwerpen, Brussel en Gent hebben elk een eigen kantoor.",
        [],
        ["Antwerpen", "Brussel", "Gent"],
    ),
    (
        "fr",
        "Mohamed Benali gère désormais le patrimoine de la famille Haddad.",
        ["Mohamed Benali", "Haddad"],
        [],
    ),
    ("fr", "LAMBERT & CIE SA demande un relevé détaillé des frais.", ["LAMBERT & CIE SA"], []),
    ("fr", "Rendez-vous avec Maître Fontaine pour l'acte de donation.", ["Fontaine"], []),
    (
        "fr",
        "Les Résidences du Parc SRL ont été vendues à Château Invest.",
        ["Les Résidences du Parc SRL", "Château Invest"],
        [],
    ),
    (
        "fr",
        "Le Groupe Rouge et Noir organise son assemblée générale en juin.",
        ["Groupe Rouge et Noir"],
        [],
    ),
    ("fr", "Utilisez Power Query pour importer les données de SharePoint.", [], []),
    (
        "en",
        "Hi team, quick one: has anyone heard back from Priya Raman about the Raman Trust?",
        ["Priya Raman", "Raman Trust"],
        [],
    ),
    (
        "en",
        "Golden Oak Ventures and Silver Lake Farms both missed the payment deadline.",
        ["Golden Oak Ventures", "Silver Lake Farms"],
        [],
    ),
    (
        "en",
        "Notes from the call with Baker: wants quarterly reporting instead of monthly.",
        ["Baker"],
        [],
    ),
    ("en", "Use Microsoft Fabric and Azure DevOps for the deployment pipeline.", [], []),
    (
        "en",
        "Client | Value\nHartwell Estates | 1.2m\nMoss & Fern Ltd | 0.8m",
        ["Hartwell Estates", "Moss & Fern Ltd"],
        [],
    ),
    (
        "en",
        "Dear all,\n\nFollowing our meeting on Tuesday I attach the revised schedule. Isabella Moreno "
        "confirmed that Moreno Family Holdings will transfer the shares before year end, and Tom "
        "Whitfield will prepare the tax note.\n\nKind regards,\nGrace Liu",
        ["Isabella Moreno", "Moreno Family Holdings", "Tom Whitfield", "Grace Liu"],
        [],
    ),
    ("nl", "De klant vraagt of de beheerder de visuals op de pagina kan aanpassen.", [], []),
]
