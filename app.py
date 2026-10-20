# -*- coding: utf-8 -*-
"""
=====================================================================
  ATELIER WORKFLOWS — Décrire les process de l'entreprise
=====================================================================

Ce que fait l'application :
  - créer plusieurs workflows (un par process de l'entreprise) ;
  - dans chaque workflow, créer des tâches principales, classées
    « Administratif » ou « Technique » ;
  - découper chaque tâche en sous-étapes, avec une note détaillée ;
  - relier les tâches entre elles avec un délai (jours, semaines, mois),
    en gérant les enchaînements simples, les choix (OU) et le parallèle (ET) ;
  - zoomer sur une tâche pour voir et modifier ses sous-étapes ;
  - sauvegarder automatiquement, exporter en JSON et PDF, réimporter un JSON.

Lancement (dans un terminal, depuis le dossier de ce fichier) :
    pip install -r requirements.txt
    streamlit run app.py

Organisation du code (dans l'ordre du fichier) :
    1. Réglages et constantes
    2. Stockage des fichiers (sauvegarde, corbeille)
    3. Modèle de données (création, contrôle des fichiers importés)
    4. Calculs (durées, récapitulatif)
    5. Schémas (Graphviz)
    6. Export PDF
    7. Historique « Annuler »
    8. Actions (fonctions appelées par les boutons)
    9. Fenêtres de confirmation
   10. Écrans (accueil, workflow, zoom sur une tâche)
   11. Programme principal
"""

import copy
import io
import json
import os
import re
import shutil
import textwrap
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from fpdf import FPDF


# =====================================================================
# 1. RÉGLAGES ET CONSTANTES
# =====================================================================

# Dossier où sont rangés les workflows (à côté de ce fichier)
DOSSIER_WORKFLOWS = Path(__file__).resolve().parent / "workflows"
DOSSIER_CORBEILLE = DOSSIER_WORKFLOWS / "corbeille"

# Identifiant du format de fichier (sert à reconnaître nos exports)
FORMAT_FICHIER = "atelier-workflows"
VERSION_FICHIER = 1

# Nombre de modifications que l'on peut annuler
TAILLE_HISTORIQUE = 30

# Unités de délai et leur équivalent en jours (pour le calcul de durée)
UNITES = {"jour": 1, "semaine": 7, "mois": 30}
UNITES_LIBELLES = {
    "jour": ("jour", "jours"),
    "semaine": ("semaine", "semaines"),
    "mois": ("mois", "mois"),
}

# Les deux métiers possibles, avec couleurs ET icône
# (l'information n'est jamais donnée par la couleur seule)
METIERS = ["Administratif", "Technique"]
STYLE_METIER = {
    "Administratif": {"icone": "📄", "fond": "#E8F0FE", "bord": "#1A56DB"},
    "Technique": {"icone": "🔧", "fond": "#FEF3E2", "bord": "#B45309"},
}

# Façon d'enchaîner quand une tâche a plusieurs tâches suivantes
SORTIES = {
    "parallele": "En même temps (ET) : toutes les tâches suivantes sont lancées",
    "alternative": "Au choix (OU) : une seule tâche suivante, selon une condition",
}

COULEUR_ACCENT = "#1A56DB"


# =====================================================================
# 2. STOCKAGE DES FICHIERS
# =====================================================================

def maintenant() -> str:
    """Date et heure actuelles au format lisible et triable."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def nouvel_id() -> str:
    """Identifiant unique court (sert aussi de nom de fichier)."""
    return uuid.uuid4().hex[:12]


def id_valide(valeur) -> bool:
    """Vérifie qu'un identifiant ne contient que des caractères sûrs
    (évite qu'un fichier importé écrive ailleurs que dans le dossier)."""
    return isinstance(valeur, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", valeur) is not None


def chemin_workflow(wf_id: str) -> Path:
    return DOSSIER_WORKFLOWS / f"{wf_id}.json"


def sauvegarder(wf: dict) -> None:
    """Enregistre le workflow sur le disque.
    On écrit d'abord un fichier temporaire, puis on le renomme :
    si l'ordinateur s'éteint pendant l'écriture, l'ancien fichier reste intact."""
    DOSSIER_WORKFLOWS.mkdir(parents=True, exist_ok=True)
    wf["modifie_le"] = maintenant()
    chemin = chemin_workflow(wf["id"])
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporaire, chemin)
    st.session_state["derniere_sauvegarde"] = datetime.now().strftime("%H:%M:%S")


def charger(wf_id: str):
    """Lit un workflow depuis le disque. Renvoie None s'il est introuvable ou illisible."""
    chemin = chemin_workflow(wf_id)
    if not chemin.exists():
        return None
    try:
        return normaliser_workflow(json.loads(chemin.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError):
        return None


def lister_workflows(dossier: Path = DOSSIER_WORKFLOWS):
    """Liste les workflows d'un dossier, du plus récemment modifié au plus ancien.
    Renvoie (liste_des_workflows, liste_des_fichiers_illisibles)."""
    workflows, illisibles = [], []
    if not dossier.exists():
        return workflows, illisibles
    for fichier in dossier.glob("*.json"):
        try:
            workflows.append(normaliser_workflow(json.loads(fichier.read_text(encoding="utf-8"))))
        except (ValueError, json.JSONDecodeError):
            illisibles.append(fichier.name)
    workflows.sort(key=lambda w: w.get("modifie_le", ""), reverse=True)
    return workflows, illisibles


def mettre_a_la_corbeille(wf_id: str) -> None:
    """Ne supprime jamais vraiment : déplace le fichier dans le dossier « corbeille »."""
    DOSSIER_CORBEILLE.mkdir(parents=True, exist_ok=True)
    source = chemin_workflow(wf_id)
    if source.exists():
        destination = DOSSIER_CORBEILLE / f"{wf_id}.json"
        if destination.exists():
            destination = DOSSIER_CORBEILLE / f"{wf_id}_{datetime.now():%Y%m%d%H%M%S}.json"
        shutil.move(str(source), str(destination))


def restaurer_de_la_corbeille(nom_fichier: str) -> None:
    """Remet un workflow de la corbeille dans la liste principale."""
    source = DOSSIER_CORBEILLE / nom_fichier
    try:
        wf = normaliser_workflow(json.loads(source.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError):
        st.session_state["alerte"] = "Ce fichier de la corbeille est illisible et ne peut pas être restauré."
        return
    if chemin_workflow(wf["id"]).exists():  # un workflow avec le même identifiant existe déjà
        wf["id"] = nouvel_id()
        wf["nom"] += " (restauré)"
    sauvegarder(wf)
    source.unlink()
    st.session_state["info"] = f"Le workflow « {wf['nom']} » a été restauré."


# =====================================================================
# 3. MODÈLE DE DONNÉES
# =====================================================================
# Structure d'un workflow (c'est aussi le contenu du fichier JSON) :
# {
#   "format": "atelier-workflows", "version": 1,
#   "id": "...", "nom": "...", "description": "...",
#   "cree_le": "...", "modifie_le": "...",
#   "taches": [
#     {
#       "id": "...", "nom": "...", "metier": "Administratif" | "Technique",
#       "note": "...",
#       "sous_etapes": [ {"id": "...", "nom": "...", "note": "..."} ],
#       "sortie": "parallele" | "alternative",   (utile si plusieurs liens)
#       "liens": [ {"id": "...", "cible": "<id tâche suivante>",
#                   "delai": 2, "unite": "jour" | "semaine" | "mois",
#                   "condition": "..."} ]
#     }
#   ]
# }

def nouveau_workflow(nom: str, description: str = "") -> dict:
    return {
        "format": FORMAT_FICHIER, "version": VERSION_FICHIER,
        "id": nouvel_id(), "nom": nom.strip(), "description": description.strip(),
        "cree_le": maintenant(), "modifie_le": maintenant(), "taches": [],
    }


def nouvelle_tache(nom: str, metier: str = "Administratif") -> dict:
    return {"id": nouvel_id(), "nom": nom.strip(), "metier": metier, "note": "",
            "sous_etapes": [], "sortie": "parallele", "liens": []}


def nouvelle_sous_etape(nom: str) -> dict:
    return {"id": nouvel_id(), "nom": nom.strip(), "note": ""}


def nouveau_lien(cible: str, delai: int = 1, unite: str = "jour", condition: str = "") -> dict:
    return {"id": nouvel_id(), "cible": cible, "delai": int(delai), "unite": unite, "condition": condition}


def _texte(valeur, defaut: str = "") -> str:
    return str(valeur).strip() if valeur not in (None, "") else defaut


def normaliser_workflow(donnees) -> dict:
    """Contrôle un workflow (chargé ou importé) et complète ce qui manque.
    Lève ValueError avec un message clair si le fichier n'est pas exploitable."""
    if not isinstance(donnees, dict) or not isinstance(donnees.get("taches"), list):
        raise ValueError("Ce fichier n'est pas un workflow de l'application : la liste des tâches est introuvable.")

    wf = {
        "format": FORMAT_FICHIER, "version": VERSION_FICHIER,
        "id": donnees["id"] if id_valide(donnees.get("id")) else nouvel_id(),
        "nom": _texte(donnees.get("nom"), "Workflow sans nom"),
        "description": _texte(donnees.get("description")),
        "cree_le": _texte(donnees.get("cree_le"), maintenant()),
        "modifie_le": _texte(donnees.get("modifie_le"), maintenant()),
        "taches": [],
    }

    ids_vus = set()
    for t in donnees["taches"]:
        if not isinstance(t, dict):
            continue
        tid = t.get("id") if id_valide(t.get("id")) and t.get("id") not in ids_vus else nouvel_id()
        ids_vus.add(tid)

        sous_etapes = []
        for s in t.get("sous_etapes") or []:
            if isinstance(s, dict):
                sous_etapes.append({
                    "id": s["id"] if id_valide(s.get("id")) else nouvel_id(),
                    "nom": _texte(s.get("nom"), "Sous-étape sans nom"),
                    "note": str(s.get("note") or ""),
                })

        liens = []
        for l in t.get("liens") or []:
            if not isinstance(l, dict) or not l.get("cible"):
                continue
            try:
                delai = max(0, min(9999, int(float(l.get("delai", 0)))))
            except (TypeError, ValueError):
                delai = 0
            liens.append({
                "id": l["id"] if id_valide(l.get("id")) else nouvel_id(),
                "cible": str(l["cible"]),
                "delai": delai,
                "unite": l.get("unite") if l.get("unite") in UNITES else "jour",
                "condition": str(l.get("condition") or ""),
            })

        wf["taches"].append({
            "id": tid,
            "nom": _texte(t.get("nom"), "Tâche sans nom"),
            "metier": t.get("metier") if t.get("metier") in METIERS else "Administratif",
            "note": str(t.get("note") or ""),
            "sous_etapes": sous_etapes,
            "sortie": t.get("sortie") if t.get("sortie") in SORTIES else "parallele",
            "liens": liens,
        })

    # On retire les liens qui pointent vers une tâche inexistante ou vers elle-même
    ids = {t["id"] for t in wf["taches"]}
    for t in wf["taches"]:
        t["liens"] = [l for l in t["liens"] if l["cible"] in ids and l["cible"] != t["id"]]
    return wf


def trouver_tache(wf: dict, tid: str):
    return next((t for t in wf["taches"] if t["id"] == tid), None)


def numero_tache(wf: dict, tid: str) -> int:
    for i, t in enumerate(wf["taches"], start=1):
        if t["id"] == tid:
            return i
    return 0


def nom_court(wf: dict, tid: str) -> str:
    """« T3 · Contrôle qualité »"""
    t = trouver_tache(wf, tid)
    return f"T{numero_tache(wf, tid)} · {t['nom']}" if t else "Tâche supprimée"


def workflow_exemple() -> dict:
    """Workflow de démonstration : réception et contrôle d'un lot.
    Il montre un enchaînement simple, un choix (OU) et du parallèle (ET)."""
    wf = nouveau_workflow("Exemple — Réception d'un lot",
                          "Exemple pour découvrir l'application. Vous pouvez le modifier ou le supprimer.")
    t1 = nouvelle_tache("Création du lot", "Administratif")
    t1["note"] = "Le magasinier enregistre le lot dès son arrivée.\n- Vérifier le bon de livraison\n- Noter le numéro fournisseur"
    t1["sous_etapes"] = [nouvelle_sous_etape("Saisir la référence fournisseur"),
                         nouvelle_sous_etape("Attribuer un numéro de lot")]
    t2 = nouvelle_tache("Contrôle qualité", "Technique")
    t2["note"] = "Contrôle par prélèvement d'échantillons au laboratoire."
    t2["sous_etapes"] = [nouvelle_sous_etape("Prélever les échantillons"),
                         nouvelle_sous_etape("Analyser au laboratoire"),
                         nouvelle_sous_etape("Rédiger le rapport de contrôle")]
    t3 = nouvelle_tache("Mise en stock", "Technique")
    t4 = nouvelle_tache("Retour au fournisseur", "Administratif")
    t5 = nouvelle_tache("Enregistrement de la facture", "Administratif")
    t6 = nouvelle_tache("Mise à jour de l'inventaire", "Technique")

    t1["liens"] = [nouveau_lien(t2["id"], 1, "jour")]
    t2["sortie"] = "alternative"
    t2["liens"] = [nouveau_lien(t3["id"], 2, "jour", "Si le lot est conforme"),
                   nouveau_lien(t4["id"], 1, "jour", "Si le lot n'est pas conforme")]
    t3["sortie"] = "parallele"
    t3["liens"] = [nouveau_lien(t5["id"], 1, "semaine"), nouveau_lien(t6["id"], 0, "jour")]
    wf["taches"] = [t1, t2, t3, t4, t5, t6]
    return wf


# =====================================================================
# 4. CALCULS : DURÉES ET RÉCAPITULATIF
# =====================================================================

def en_jours(lien: dict) -> int:
    return int(lien["delai"]) * UNITES.get(lien["unite"], 1)


def libelle_delai(lien: dict) -> str:
    """« 2 semaines », « 1 jour », « Sans délai »"""
    n = int(lien["delai"])
    if n == 0:
        return "Sans délai"
    singulier, pluriel = UNITES_LIBELLES.get(lien["unite"], ("jour", "jours"))
    return f"{n} {singulier if n == 1 else pluriel}"


def _nombre(x: float) -> str:
    x = round(x, 1)
    return str(int(x)) if x == int(x) else str(x).replace(".", ",")


def formater_jours(j: int) -> str:
    if j < 7:
        return f"{j} jour{'s' if j > 1 else ''}"
    if j < 30:
        return f"{j} jours (≈ {_nombre(j / 7)} sem.)"
    return f"{j} jours (≈ {_nombre(j / 30)} mois)"


def calculer_durees(wf: dict):
    """Durée totale estimée du workflow, en jours : (minimum, maximum, boucle_détectée).
    - Tâches en parallèle (ET) : on attend la branche la plus longue.
    - Choix (OU) : le minimum prend la branche la plus courte, le maximum la plus longue.
    - Une boucle (retour en arrière) n'est comptée qu'une fois."""
    taches = {t["id"]: t for t in wf["taches"]}
    boucle = [False]

    def plus_long(tid, mode, pile):
        if tid in pile:
            boucle[0] = True
            return 0
        t = taches[tid]
        valeurs = [en_jours(l) + plus_long(l["cible"], mode, pile | {tid})
                   for l in t["liens"] if l["cible"] in taches]
        if not valeurs:
            return 0
        if t["sortie"] == "alternative" and len(valeurs) > 1 and mode == "min":
            return min(valeurs)
        return max(valeurs)

    entrants = {l["cible"] for t in wf["taches"] for l in t["liens"]}
    departs = [tid for tid in taches if tid not in entrants] or list(taches)[:1]
    if not departs:
        return 0, 0, False
    mini = max(plus_long(d, "min", frozenset()) for d in departs)
    maxi = max(plus_long(d, "max", frozenset()) for d in departs)
    return mini, maxi, boucle[0]


def texte_duree(wf: dict) -> str:
    mini, maxi, _ = calculer_durees(wf)
    if mini == maxi:
        return formater_jours(maxi)
    return f"{formater_jours(mini)} à {formater_jours(maxi)}"


def compter(wf: dict) -> dict:
    """Nombre de tâches et de sous-étapes par métier."""
    resultat = {m: {"taches": 0, "sous_etapes": 0} for m in METIERS}
    for t in wf["taches"]:
        resultat[t["metier"]]["taches"] += 1
        resultat[t["metier"]]["sous_etapes"] += len(t["sous_etapes"])
    return resultat


def tableau_recap(wf: dict) -> pd.DataFrame:
    """Tableau récapitulatif Administratif / Technique / Total."""
    c = compter(wf)
    total = sum(v["taches"] for v in c.values())
    lignes = []
    for m in METIERS:
        part = f"{round(100 * c[m]['taches'] / total)} %" if total else "—"
        lignes.append({"Métier": f"{STYLE_METIER[m]['icone']} {m}", "Tâches": c[m]["taches"],
                       "Sous-étapes": c[m]["sous_etapes"], "Part des tâches": part})
    lignes.append({"Métier": "Total", "Tâches": total,
                   "Sous-étapes": sum(v["sous_etapes"] for v in c.values()),
                   "Part des tâches": "100 %" if total else "—"})
    return pd.DataFrame(lignes)


def texte_suite(wf: dict, t: dict) -> str:
    """Phrase qui décrit ce qui se passe après une tâche."""
    liens = t["liens"]
    if not liens:
        return "Ensuite : fin du workflow."
    morceaux = []
    for l in liens:
        bout = f"{nom_court(wf, l['cible'])} ({libelle_delai(l).lower()})"
        if t["sortie"] == "alternative" and len(liens) > 1:
            bout = f"{l['condition'] or 'condition à préciser'} → {bout}"
        morceaux.append(bout)
    if len(liens) == 1:
        return f"Ensuite : {morceaux[0]}"
    if t["sortie"] == "alternative":
        return "Ensuite, au choix (OU) : " + " ; ".join(morceaux)
    return "Ensuite, en même temps (ET) : " + " ; ".join(morceaux)


# =====================================================================
# 5. SCHÉMAS (GRAPHVIZ)
# =====================================================================

def _dot(texte: str) -> str:
    """Protège un texte pour l'écrire dans un schéma Graphviz."""
    return texte.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _couper(texte: str, largeur: int = 24) -> str:
    """Coupe les noms longs sur plusieurs lignes."""
    return "\n".join(textwrap.wrap(texte, largeur)) or texte


def schema_workflow(wf: dict, surligner: str = None, pour_image: bool = False):
    """Schéma de tout le workflow : Début → tâches → Fin, avec délais, OU et ET."""
    taches = wf["taches"]
    if not taches:
        return None
    lignes = [
        "digraph G {",
        "rankdir=LR;",
        f'graph [fontname="Helvetica", bgcolor="{"white" if pour_image else "transparent"}", '
        f'nodesep=0.45, ranksep=0.65, pad=0.25{", dpi=160" if pour_image else ""}];',
        'node [fontname="Helvetica", fontsize=12, fontcolor="#1F2937"];',
        'edge [fontname="Helvetica", fontsize=10, color="#475569", fontcolor="#1F2937", arrowsize=0.8];',
        'debut [label="Début", shape=circle, style=filled, fillcolor="#DCFCE7", color="#15803D", '
        'fontsize=10, width=0.75, fixedsize=true];',
        'fin [label="Fin", shape=doublecircle, style=filled, fillcolor="#FEE2E2", color="#B91C1C", '
        'fontsize=10, width=0.6, fixedsize=true];',
    ]
    entrants = {l["cible"] for t in taches for l in t["liens"]}

    for i, t in enumerate(taches, start=1):
        style = STYLE_METIER[t["metier"]]
        nb = len(t["sous_etapes"])
        detail = t["metier"] + (f" · {nb} sous-étape{'s' if nb > 1 else ''}" if nb else "")
        etiquette = f"T{i} · {_couper(t['nom'])}\n{detail}"
        epaisseur = 3.5 if t["id"] == surligner else 1.5
        lignes.append(
            f'"{t["id"]}" [label="{_dot(etiquette)}", shape=box, style="rounded,filled", '
            f'fillcolor="{style["fond"]}", color="{style["bord"]}", penwidth={epaisseur}, margin="0.2,0.1"];'
        )

    departs = [t for t in taches if t["id"] not in entrants] or taches[:1]
    for t in departs:
        lignes.append(f'debut -> "{t["id"]}";')

    for t in taches:
        liens = t["liens"]
        if not liens:
            lignes.append(f'"{t["id"]}" -> fin;')
        elif len(liens) == 1:
            lignes.append(f'"{t["id"]}" -> "{liens[0]["cible"]}" [label="{_dot(libelle_delai(liens[0]))}"];')
        else:
            # Losange de choix (OU) ou de parallèle (ET), comme en BPMN
            porte = f"porte_{t['id']}"
            choix = t["sortie"] == "alternative"
            lignes.append(
                f'"{porte}" [label="{"OU" if choix else "ET"}", shape=diamond, style=filled, '
                f'fillcolor="{"#FEF9C3" if choix else "#E0E7FF"}", color="{"#A16207" if choix else "#4338CA"}", '
                f'fontsize=10, width=0.65, height=0.65, fixedsize=true];'
            )
            lignes.append(f'"{t["id"]}" -> "{porte}" [arrowsize=0.6];')
            for l in liens:
                texte = libelle_delai(l)
                if choix:
                    texte = f"{_couper(l['condition'] or 'Condition à préciser', 20)}\n{texte}"
                lignes.append(f'"{porte}" -> "{l["cible"]}" [label="{_dot(texte)}"];')

    lignes.append("}")
    return "\n".join(lignes)


def schema_sous_etapes(t: dict):
    """Mini-schéma des sous-étapes d'une tâche (de gauche à droite, ou de haut en bas si nombreuses)."""
    if not t["sous_etapes"]:
        return None
    sens = "LR" if len(t["sous_etapes"]) <= 4 else "TB"
    style = STYLE_METIER[t["metier"]]
    lignes = [
        "digraph S {", f"rankdir={sens};",
        'graph [bgcolor="transparent", nodesep=0.35, ranksep=0.4, pad=0.15];',
        f'node [shape=box, style="rounded,filled", fillcolor="white", color="{style["bord"]}", '
        'fontname="Helvetica", fontsize=11, fontcolor="#1F2937", margin="0.15,0.08"];',
        'edge [color="#475569", arrowsize=0.7];',
    ]
    precedent = None
    for i, s in enumerate(t["sous_etapes"], start=1):
        lignes.append(f'"{s["id"]}" [label="{_dot(f"{i}. " + _couper(s["nom"], 22))}"];')
        if precedent:
            lignes.append(f'"{precedent}" -> "{s["id"]}";')
        precedent = s["id"]
    lignes.append("}")
    return "\n".join(lignes)


def image_schema(dot: str):
    """Transforme le schéma en image PNG pour le PDF.
    Nécessite le logiciel Graphviz installé sur l'ordinateur ; sinon renvoie None."""
    try:
        import graphviz  # bibliothèque Python facultative
        if not shutil.which("dot"):
            return None
        return graphviz.Source(dot).pipe(format="png")
    except Exception:
        return None


# =====================================================================
# 6. EXPORT PDF
# =====================================================================

def trouver_police():
    """Cherche une police qui gère bien les accents (Windows, Mac, Linux)."""
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidats = [
        (windows / "arial.ttf", windows / "arialbd.ttf"),
        (Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
        (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for normale, grasse in candidats:
        if normale.exists() and grasse.exists():
            return str(normale), str(grasse)
    return None


REMPLACEMENTS_PDF = {"→": "->", "←": "<-", "≈": "env.", "\u00a0": " ", "\u202f": " "}
REMPLACEMENTS_LATIN = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                       "…": "...", "•": "-", "œ": "oe", "Œ": "OE", "€": "EUR"}


def texte_pdf(texte, unicode: bool) -> str:
    """Nettoie un texte pour le PDF (retire émojis et symboles non imprimables)."""
    s = str(texte or "")
    for a, b in REMPLACEMENTS_PDF.items():
        s = s.replace(a, b)
    s = "".join(c for c in s if ord(c) < 0x2100 or c in "€")
    if not unicode:
        for a, b in REMPLACEMENTS_LATIN.items():
            s = s.replace(a, b)
        s = s.encode("latin-1", "replace").decode("latin-1")
    return s


class PDFWorkflow(FPDF):
    """Mise en page du PDF : en-tête avec le nom du workflow, pied de page numéroté."""

    def __init__(self, titre: str):
        super().__init__(format="A4")
        polices = trouver_police()
        if polices:
            self.add_font("Police", "", polices[0])
            self.add_font("Police", "B", polices[1])
            self.police, self.unicode = "Police", True
        else:
            self.police, self.unicode = "Helvetica", False
        self.titre_doc = titre
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(15, 15, 15)

    def t(self, texte) -> str:
        return texte_pdf(texte, self.unicode)

    def header(self):
        self.set_font(self.police, "", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 6, self.t(self.titre_doc), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font(self.police, "", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 6, self.t(f"Exporté le {datetime.now():%d/%m/%Y à %H:%M}"), align="L")
        self.set_x(self.l_margin)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R")

    def titre(self, texte: str, taille: int = 14):
        self.set_font(self.police, "B", taille)
        self.set_text_color(31, 41, 55)
        self.multi_cell(0, taille * 0.5, self.t(texte), new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)

    def paragraphe(self, texte: str, taille: int = 10.5, gras: bool = False, gris: bool = False):
        self.set_font(self.police, "B" if gras else "", taille)
        self.set_text_color(*((100, 116, 139) if gris else (31, 41, 55)))
        self.multi_cell(0, 5.5, self.t(texte), new_x="LMARGIN", new_y="NEXT")


def generer_pdf(wf: dict) -> bytes:
    """Crée le PDF complet du workflow : synthèse, schéma, détail des tâches."""
    pdf = PDFWorkflow(f"Workflow : {wf['nom']}")

    # --- Page 1 : synthèse ---
    pdf.add_page()
    pdf.titre(wf["nom"], 20)
    if wf["description"]:
        pdf.paragraphe(wf["description"], gris=True)
    pdf.ln(3)
    pdf.paragraphe(f"Nombre de tâches : {len(wf['taches'])}", gras=True)
    pdf.paragraphe(f"Durée totale estimée : {texte_duree(wf)}", gras=True)
    pdf.paragraphe("Calcul : délais du chemin le plus long (1 semaine = 7 jours, 1 mois = 30 jours).",
                   taille=9, gris=True)
    pdf.ln(4)

    pdf.titre("Récapitulatif par métier", 13)
    recap = tableau_recap(wf)
    pdf.set_font(pdf.police, "", 10.5)
    pdf.set_text_color(31, 41, 55)
    with pdf.table(width=160, col_widths=(55, 30, 35, 40), align="LEFT",
                   text_align=("LEFT", "CENTER", "CENTER", "CENTER"), line_height=7) as tableau:
        ligne = tableau.row()
        for entete in recap.columns:
            ligne.cell(pdf.t(entete))
        for _, r in recap.iterrows():
            ligne = tableau.row()
            for valeur in r:
                ligne.cell(pdf.t(valeur).strip())

    # --- Page 2 : schéma (format paysage) ---
    dot = schema_workflow(wf, pour_image=True)
    if dot:
        png = image_schema(dot)
        pdf.add_page(orientation="L")
        pdf.titre("Schéma du workflow", 13)
        if png:
            pdf.image(io.BytesIO(png), x=pdf.l_margin, w=pdf.epw, h=pdf.eph - 22, keep_aspect_ratio=True)
        else:
            pdf.paragraphe("Le schéma n'a pas pu être dessiné : installez le logiciel Graphviz sur "
                           "l'ordinateur (voir le fichier LISEZMOI) pour l'ajouter au PDF.", gris=True)

    # --- Pages suivantes : détail de chaque tâche ---
    pdf.add_page(orientation="P")
    pdf.titre("Détail des tâches", 15)
    if not wf["taches"]:
        pdf.paragraphe("Ce workflow ne contient encore aucune tâche.", gris=True)
    for i, t in enumerate(wf["taches"], start=1):
        pdf.ln(3)
        pdf.set_draw_color(203, 213, 225)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + pdf.epw, pdf.get_y())
        pdf.ln(2)
        pdf.titre(f"T{i} · {t['nom']}", 12.5)
        pdf.paragraphe(f"Métier : {t['metier']}", gras=True)
        if t["note"].strip():
            pdf.paragraphe("Note :", gras=True)
            pdf.paragraphe(t["note"])
        if t["sous_etapes"]:
            pdf.paragraphe(f"Sous-étapes ({len(t['sous_etapes'])}) :", gras=True)
            for j, s in enumerate(t["sous_etapes"], start=1):
                pdf.paragraphe(f"   {i}.{j}  {s['nom']}")
                if s["note"].strip():
                    pdf.set_x(pdf.l_margin + 10)
                    pdf.set_font(pdf.police, "", 9.5)
                    pdf.set_text_color(100, 116, 139)
                    pdf.multi_cell(pdf.epw - 10, 5, pdf.t(s["note"]), new_x="LMARGIN", new_y="NEXT")
        pdf.paragraphe(texte_suite(wf, t).replace("→", "->"), gris=True)

    return bytes(pdf.output())


# =====================================================================
# 7. HISTORIQUE « ANNULER »
# =====================================================================

def memoriser(wf: dict) -> None:
    """Garde une copie du workflow AVANT chaque modification."""
    pile = st.session_state["historique"].setdefault(wf["id"], [])
    pile.append(copy.deepcopy(wf))
    del pile[:-TAILLE_HISTORIQUE]


def peut_annuler(wf: dict) -> bool:
    return bool(st.session_state["historique"].get(wf["id"]))


def annuler() -> None:
    wf = st.session_state["wf"]
    pile = st.session_state["historique"].get(wf["id"])
    if not pile:
        return
    precedent = pile.pop()
    st.session_state["wf"] = precedent
    sauvegarder(precedent)
    rafraichir_champs()
    st.session_state["info"] = "La dernière modification a été annulée."
    if st.session_state["page"] == "tache" and not trouver_tache(precedent, st.session_state["tache_id"]):
        st.session_state["page"] = "workflow"


def rafraichir_champs() -> None:
    """Force les champs de saisie à relire les données (après annulation ou import)."""
    st.session_state["rev"] += 1


def modifier(action, *args) -> None:
    """Applique une modification : copie pour « Annuler », action, puis sauvegarde."""
    wf = st.session_state["wf"]
    memoriser(wf)
    action(wf, *args)
    sauvegarder(wf)


# =====================================================================
# 8. ACTIONS (appelées par les boutons et les champs)
# =====================================================================

# ---- Navigation -----------------------------------------------------

def aller_accueil():
    st.session_state.update(page="accueil", wf=None, tache_id=None)


def ouvrir_workflow(wf_id: str):
    wf = charger(wf_id)
    if wf is None:
        st.session_state["alerte"] = "Ce workflow est introuvable ou illisible. Il a peut-être été déplacé."
        return
    st.session_state.update(page="workflow", wf=wf, tache_id=None)
    rafraichir_champs()


def ouvrir_tache(tid: str):
    st.session_state.update(page="tache", tache_id=tid)


def retour_workflow():
    st.session_state.update(page="workflow", tache_id=None)


# ---- Workflow -------------------------------------------------------

def cb_champ_workflow(champ: str, cle: str):
    valeur = st.session_state[cle].strip()
    if champ == "nom" and not valeur:
        st.session_state["alerte"] = "Le nom du workflow ne peut pas être vide : l'ancien nom a été conservé."
        rafraichir_champs()
        return
    modifier(lambda wf: wf.__setitem__(champ, valeur))


def dupliquer_workflow():
    copie = copy.deepcopy(st.session_state["wf"])
    copie.update(id=nouvel_id(), nom=copie["nom"] + " (copie)", cree_le=maintenant())
    sauvegarder(copie)
    ouvrir_workflow(copie["id"])
    st.session_state["info"] = "Copie créée : vous travaillez maintenant sur la copie."


def creer_exemple():
    wf = workflow_exemple()
    sauvegarder(wf)
    ouvrir_workflow(wf["id"])


# ---- Tâches ---------------------------------------------------------

def cb_champ_tache(tid: str, champ: str, cle: str):
    valeur = st.session_state[cle]
    if champ == "nom":
        valeur = valeur.strip()
        if not valeur:
            st.session_state["alerte"] = "Le nom de la tâche ne peut pas être vide : l'ancien nom a été conservé."
            rafraichir_champs()
            return
    modifier(lambda wf: trouver_tache(wf, tid).__setitem__(champ, valeur))


def deplacer(liste: list, element_id: str, sens: int) -> None:
    """Monte (sens = -1) ou descend (sens = +1) un élément dans une liste."""
    i = next(k for k, e in enumerate(liste) if e["id"] == element_id)
    j = i + sens
    if 0 <= j < len(liste):
        liste[i], liste[j] = liste[j], liste[i]


def deplacer_tache(tid: str, sens: int):
    modifier(lambda wf: deplacer(wf["taches"], tid, sens))


def supprimer_tache(wf: dict, tid: str):
    wf["taches"] = [t for t in wf["taches"] if t["id"] != tid]
    for t in wf["taches"]:
        t["liens"] = [l for l in t["liens"] if l["cible"] != tid]


# ---- Sous-étapes ----------------------------------------------------

def cb_champ_sous_etape(tid: str, sid: str, champ: str, cle: str):
    valeur = st.session_state[cle]
    if champ == "nom":
        valeur = valeur.strip()
        if not valeur:
            st.session_state["alerte"] = "Le nom de la sous-étape ne peut pas être vide : l'ancien nom a été conservé."
            rafraichir_champs()
            return

    def action(wf):
        s = next(s for s in trouver_tache(wf, tid)["sous_etapes"] if s["id"] == sid)
        s[champ] = valeur
    modifier(action)


def deplacer_sous_etape(tid: str, sid: str, sens: int):
    modifier(lambda wf: deplacer(trouver_tache(wf, tid)["sous_etapes"], sid, sens))


# ---- Liens (enchaînement entre tâches) ------------------------------

def cb_champ_lien(tid: str, lid: str, champ: str, cle: str):
    valeur = st.session_state[cle]
    if champ == "delai":
        valeur = int(valeur or 0)

    def action(wf):
        lien = next(l for l in trouver_tache(wf, tid)["liens"] if l["id"] == lid)
        lien[champ] = valeur
    modifier(action)


def ajouter_lien(tid: str):
    wf = st.session_state["wf"]
    t = trouver_tache(wf, tid)
    deja = {l["cible"] for l in t["liens"]}
    possibles = [x["id"] for x in wf["taches"] if x["id"] != tid and x["id"] not in deja]
    if not possibles:
        st.session_state["alerte"] = ("Toutes les autres tâches sont déjà reliées à celle-ci. "
                                      "Créez d'abord une nouvelle tâche dans le workflow.")
        return
    # Par défaut : la tâche qui suit dans la liste, sinon la première disponible
    ordre = [x["id"] for x in wf["taches"]]
    apres = [p for p in possibles if ordre.index(p) > ordre.index(tid)]
    cible = (apres or possibles)[0]
    modifier(lambda w: trouver_tache(w, tid)["liens"].append(nouveau_lien(cible)))


def supprimer_lien(tid: str, lid: str):
    def action(wf):
        t = trouver_tache(wf, tid)
        t["liens"] = [l for l in t["liens"] if l["id"] != lid]
    modifier(action)
    st.session_state["info"] = "Lien supprimé. Vous pouvez revenir en arrière avec « Annuler »."


# ---- Import ---------------------------------------------------------

def cb_import():
    """Lit le fichier JSON choisi dans la barre latérale."""
    cle = f"import_{st.session_state['import_n']}"
    fichier = st.session_state.get(cle)
    st.session_state["import_n"] += 1  # vide le sélecteur de fichier pour le prochain import
    if fichier is None:
        return
    try:
        wf = normaliser_workflow(json.loads(fichier.getvalue().decode("utf-8-sig")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as erreur:
        message = str(erreur) if isinstance(erreur, ValueError) and "workflow" in str(erreur) else ""
        st.session_state["alerte"] = ("Ce fichier ne peut pas être importé. Vérifiez qu'il s'agit bien d'un "
                                      "export JSON de l'application. " + message)
        return
    if chemin_workflow(wf["id"]).exists():
        st.session_state["import_attente"] = wf  # on demandera quoi faire
    else:
        terminer_import(wf)


def terminer_import(wf: dict, comme_copie: bool = False):
    if comme_copie:
        wf.update(id=nouvel_id(), nom=wf["nom"] + " (importé)")
    sauvegarder(wf)
    ouvrir_workflow(wf["id"])
    st.session_state["import_attente"] = None
    st.session_state["info"] = f"Workflow « {wf['nom']} » importé. Vous pouvez le modifier."


def abandonner_import():
    st.session_state["import_attente"] = None


# =====================================================================
# 9. FENÊTRES DE CONFIRMATION
# =====================================================================

@st.dialog("Supprimer ce workflow ?")
def fenetre_supprimer_workflow():
    wf = st.session_state["wf"]
    st.write(f"Le workflow **{echapper(wf['nom'])}** et ses {len(wf['taches'])} tâches "
             "seront déplacés dans la **corbeille**.")
    st.caption("Vous pourrez le restaurer depuis « Corbeille » dans le menu de gauche.")
    c1, c2 = st.columns(2)
    if c1.button("Oui, mettre à la corbeille", type="primary", width="stretch"):
        mettre_a_la_corbeille(wf["id"])
        st.session_state["info"] = f"« {wf['nom']} » a été placé dans la corbeille."
        aller_accueil()
        st.rerun()
    if c2.button("Non, garder", width="stretch"):
        st.rerun()


@st.dialog("Supprimer cette tâche ?")
def fenetre_supprimer_tache(tid: str):
    wf = st.session_state["wf"]
    t = trouver_tache(wf, tid)
    nb = len(t["sous_etapes"])
    st.write(f"La tâche **{echapper(nom_court(wf, tid))}**, ses {nb} sous-étape{'s' if nb > 1 else ''} "
             "et les flèches qui la relient seront supprimées.")
    st.caption("En cas d'erreur : bouton « ↩ Annuler la dernière modification ».")
    c1, c2 = st.columns(2)
    if c1.button("Oui, supprimer", type="primary", width="stretch"):
        modifier(supprimer_tache, tid)
        st.session_state["info"] = "Tâche supprimée."
        retour_workflow()
        st.rerun()
    if c2.button("Non, garder", width="stretch"):
        st.rerun()


@st.dialog("Supprimer cette sous-étape ?")
def fenetre_supprimer_sous_etape(tid: str, sid: str):
    t = trouver_tache(st.session_state["wf"], tid)
    s = next(s for s in t["sous_etapes"] if s["id"] == sid)
    st.write(f"La sous-étape **{echapper(s['nom'])}** et sa note seront supprimées.")
    st.caption("En cas d'erreur : bouton « ↩ Annuler la dernière modification ».")
    c1, c2 = st.columns(2)
    if c1.button("Oui, supprimer", type="primary", width="stretch"):
        def action(wf):
            tache = trouver_tache(wf, tid)
            tache["sous_etapes"] = [x for x in tache["sous_etapes"] if x["id"] != sid]
        modifier(action)
        st.rerun()
    if c2.button("Non, garder", width="stretch"):
        st.rerun()


@st.dialog("Ajouter une tâche", width="medium")
def fenetre_ajouter_tache():
    wf = st.session_state["wf"]
    options_apres = [None] + [t["id"] for t in wf["taches"]]
    with st.form("form_ajout_tache", border=False):
        nom = st.text_input("Nom de la tâche *", placeholder="ex. : Création du lot")
        metier = st.radio("Métier", METIERS, horizontal=True,
                          format_func=lambda m: f"{STYLE_METIER[m]['icone']} {m}")
        apres = st.selectbox(
            "Cette tâche vient après…", options_apres, index=len(options_apres) - 1,
            format_func=lambda x: "Aucune (tâche de départ ou à relier plus tard)" if x is None else nom_court(wf, x),
        )
        c1, c2 = st.columns(2)
        delai = c1.number_input("Délai avant cette tâche", min_value=0, max_value=9999, value=1, step=1,
                                help="Temps entre la fin de la tâche précédente et le début de celle-ci.")
        unite = c2.selectbox("Unité", list(UNITES), format_func=lambda u: UNITES_LIBELLES[u][1])
        valider = st.form_submit_button("➕ Ajouter la tâche", type="primary", width="stretch")

    if valider:
        if not nom.strip():
            st.error("Indiquez un nom pour la tâche, puis cliquez à nouveau sur « Ajouter la tâche ».")
            return
        tache = nouvelle_tache(nom, metier)

        def action(w):
            if apres is None:
                w["taches"].append(tache)
            else:
                position = next(i for i, t in enumerate(w["taches"]) if t["id"] == apres)
                w["taches"].insert(position + 1, tache)
                trouver_tache(w, apres)["liens"].append(nouveau_lien(tache["id"], delai, unite))
        modifier(action)

        precedente = trouver_tache(wf, apres) if apres else None
        if precedente and len(precedente["liens"]) == 2:
            st.session_state["info"] = (f"Tâche ajoutée. « {precedente['nom']} » a maintenant 2 tâches suivantes : "
                                        "ouvrez-la pour préciser si elles se font en même temps (ET) ou au choix (OU).")
        else:
            st.session_state["info"] = f"Tâche « {tache['nom']} » ajoutée."
        st.rerun()


@st.dialog("Ce workflow existe déjà")
def fenetre_conflit_import():
    wf = st.session_state["import_attente"]
    st.write(f"Un workflow identique (**{echapper(wf['nom'])}**) est déjà enregistré dans l'application. "
             "Que voulez-vous faire ?")
    if st.button("Remplacer la version enregistrée", type="primary", width="stretch",
                 help="La version actuelle est d'abord mise à la corbeille, par sécurité."):
        mettre_a_la_corbeille(wf["id"])
        terminer_import(wf)
        st.rerun()
    if st.button("Garder les deux (importer comme copie)", width="stretch"):
        terminer_import(wf, comme_copie=True)
        st.rerun()
    if st.button("Ne rien importer", width="stretch"):
        abandonner_import()
        st.rerun()


# =====================================================================
# 10. ÉCRANS
# =====================================================================

def echapper(texte: str) -> str:
    """Empêche qu'un nom contenant * _ # etc. soit interprété comme mise en forme."""
    return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~$])", r"\\\1", str(texte))


def nom_fichier(wf: dict, extension: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", wf["nom"].encode("ascii", "ignore").decode()).strip("-").lower()
    return f"workflow_{base or wf['id']}.{extension}"


def injecter_style():
    """Apparence : couleur d'accent, tailles lisibles, zones cliquables de 44 px, focus visible."""
    st.markdown(f"""
    <style>
      html, body, [data-testid="stAppViewContainer"] {{ font-size: 16px; }}
      .block-container {{ padding-top: 2rem; max-width: 1200px; }}
      .stButton button, .stDownloadButton button, .stFormSubmitButton button {{
          min-height: 44px; border-radius: 10px; font-size: 1rem;
      }}
      button[kind="primary"], [data-testid="stBaseButton-primary"],
      [data-testid="stBaseButton-primaryFormSubmit"] {{
          background-color: {COULEUR_ACCENT} !important; border-color: {COULEUR_ACCENT} !important; color: #fff !important;
      }}
      button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {{
          background-color: #1E429F !important; border-color: #1E429F !important;
      }}
      *:focus-visible {{ outline: 3px solid {COULEUR_ACCENT} !important; outline-offset: 2px; }}
      .fil-ariane {{ color: #475569; font-size: 0.95rem; margin-bottom: .25rem; }}
      .badge {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .9rem;
               font-weight: 600; border: 1.5px solid; }}
      .badge-Administratif {{ background:#E8F0FE; color:#1E429F; border-color:#1A56DB; }}
      .badge-Technique {{ background:#FEF3E2; color:#8A3A00; border-color:#B45309; }}
    </style>
    """, unsafe_allow_html=True)


def badge(metier: str) -> str:
    return f'<span class="badge badge-{metier}">{STYLE_METIER[metier]["icone"]} {metier}</span>'


def afficher_messages():
    """Affiche une seule fois les messages d'information ou d'alerte en attente."""
    if st.session_state.get("alerte"):
        st.warning(st.session_state.pop("alerte"), icon="⚠️")
    if st.session_state.get("info"):
        st.success(st.session_state.pop("info"), icon="✅")


def barre_actions(wf: dict, avec_retour_workflow: bool = False):
    """Ligne d'actions commune : retour, annuler, état de la sauvegarde."""
    c1, c2, c3 = st.columns([1.2, 1.6, 2], vertical_alignment="center")
    if avec_retour_workflow:
        c1.button("← Retour au workflow", on_click=retour_workflow, width="stretch")
    else:
        c1.button("← Tous les workflows", on_click=aller_accueil, width="stretch")
    c2.button("↩ Annuler la dernière modification", on_click=annuler, disabled=not peut_annuler(wf),
              width="stretch", help="Revient à l'état précédent (jusqu'à 30 modifications).")
    heure = st.session_state.get("derniere_sauvegarde")
    c3.caption(f"💾 Enregistré automatiquement{f' à {heure}' if heure else ''}")


# ---- Barre latérale -------------------------------------------------

def barre_laterale(workflows: list):
    with st.sidebar:
        st.markdown("## 🗂️ Atelier Workflows")
        st.button("🏠 Accueil", on_click=aller_accueil, width="stretch")

        st.markdown("#### Mes workflows")
        if not workflows:
            st.caption("Aucun workflow pour l'instant.")
        actuel = st.session_state["wf"]["id"] if st.session_state.get("wf") else None
        for w in workflows:
            st.button(w["nom"], key=f"nav_{w['id']}", on_click=ouvrir_workflow, args=(w["id"],),
                      type="primary" if w["id"] == actuel else "secondary", width="stretch")

        st.divider()
        st.markdown("#### Importer un workflow")
        st.file_uploader("Fichier JSON exporté depuis l'application", type=["json"],
                         key=f"import_{st.session_state['import_n']}", on_change=cb_import,
                         help="Pour reprendre un workflow exporté, ou le récupérer sur un autre ordinateur.")

        corbeille = sorted(DOSSIER_CORBEILLE.glob("*.json")) if DOSSIER_CORBEILLE.exists() else []
        if corbeille:
            with st.expander(f"🗑️ Corbeille ({len(corbeille)})"):
                for f in corbeille:
                    try:
                        nom = json.loads(f.read_text(encoding="utf-8")).get("nom", f.stem)
                    except (ValueError, OSError):
                        nom = f.stem
                    st.button(f"Restaurer « {nom} »", key=f"rest_{f.name}",
                              on_click=restaurer_de_la_corbeille, args=(f.name,), width="stretch")


# ---- Écran 1 : accueil ----------------------------------------------

def page_accueil(workflows: list, illisibles: list):
    st.title("🗂️ Atelier Workflows")
    st.markdown("Décrivez les process de votre entreprise : **tâches**, **sous-étapes**, **délais** "
                "et **enchaînements**. Tout est enregistré automatiquement sur cet ordinateur.")
    afficher_messages()
    if illisibles:
        st.warning("Certains fichiers du dossier « workflows » sont illisibles et ont été ignorés : "
                   + ", ".join(illisibles), icon="⚠️")

    with st.expander("➕ Créer un nouveau workflow", expanded=not workflows):
        with st.form("form_creer", border=False):
            nom = st.text_input("Nom du workflow *", placeholder="ex. : Réception d'un lot")
            description = st.text_area("Description (facultatif)", placeholder="À quoi sert ce process ? Qui est concerné ?",
                                       height=90)
            creer = st.form_submit_button("➕ Créer le workflow", type="primary")
        if creer:
            if not nom.strip():
                st.error("Indiquez un nom pour le workflow, puis cliquez à nouveau sur « Créer le workflow ».")
            else:
                wf = nouveau_workflow(nom, description)
                sauvegarder(wf)
                ouvrir_workflow(wf["id"])
                st.rerun()

    if not workflows:
        with st.container(border=True):
            st.markdown("### Aucun workflow pour l'instant")
            st.markdown("1. Donnez un nom à votre premier workflow ci-dessus.\n"
                        "2. Ajoutez ses tâches, puis ouvrez chaque tâche pour décrire ses sous-étapes.\n\n"
                        "Vous pouvez aussi **importer un fichier JSON** depuis le menu de gauche, "
                        "ou découvrir l'outil avec un exemple.")
            st.button("👀 Voir un exemple", on_click=creer_exemple)
        return

    # Tableau récapitulatif de tous les workflows
    st.subheader("Vue d'ensemble")
    lignes = []
    for w in workflows:
        c = compter(w)
        lignes.append({
            "Workflow": w["nom"],
            "📄 Administratives": c["Administratif"]["taches"],
            "🔧 Techniques": c["Technique"]["taches"],
            "Total tâches": len(w["taches"]),
            "Durée estimée": texte_duree(w) if w["taches"] else "—",
            "Modifié le": w["modifie_le"],
        })
    st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch")

    # Cartes cliquables
    st.subheader("Ouvrir un workflow")
    colonnes = st.columns(3)
    for i, w in enumerate(workflows):
        c = compter(w)
        with colonnes[i % 3].container(border=True):
            st.markdown(f"**{echapper(w['nom'])}**")
            if w["description"]:
                st.caption(w["description"][:140] + ("…" if len(w["description"]) > 140 else ""))
            st.markdown(f"{badge('Administratif')} {c['Administratif']['taches']} &nbsp; "
                        f"{badge('Technique')} {c['Technique']['taches']}", unsafe_allow_html=True)
            st.caption(f"⏱️ {texte_duree(w) if w['taches'] else 'pas encore de tâche'}")
            st.button("Ouvrir", key=f"ouvrir_{w['id']}", on_click=ouvrir_workflow, args=(w["id"],),
                      type="primary", width="stretch")


# ---- Écran 2 : un workflow ------------------------------------------

def page_workflow(wf: dict):
    rev = st.session_state["rev"]
    st.markdown(f'<div class="fil-ariane">Accueil › <strong>{echapper(wf["nom"])}</strong></div>',
                unsafe_allow_html=True)
    st.title(wf["nom"])
    barre_actions(wf)
    afficher_messages()

    # Exports
    c1, c2, _ = st.columns([1, 1, 2])
    c1.download_button("⬇️ Exporter en JSON", data=json.dumps(wf, ensure_ascii=False, indent=2),
                       file_name=nom_fichier(wf, "json"), mime="application/json", on_click="ignore",
                       width="stretch", help="Fichier à garder pour réimporter et modifier le workflow plus tard.")
    instantane = copy.deepcopy(wf)
    c2.download_button("⬇️ Exporter en PDF", data=lambda: generer_pdf(instantane),
                       file_name=nom_fichier(wf, "pdf"), mime="application/pdf", on_click="ignore",
                       width="stretch", help="Document à lire, imprimer ou partager.")

    with st.expander("✏️ Nom, description, copie, suppression"):
        st.text_input("Nom du workflow", value=wf["nom"], key=f"wf_nom_{rev}",
                      on_change=cb_champ_workflow, args=("nom", f"wf_nom_{rev}"))
        st.text_area("Description", value=wf["description"], key=f"wf_desc_{rev}", height=90,
                     on_change=cb_champ_workflow, args=("description", f"wf_desc_{rev}"))
        d1, d2, _ = st.columns([1, 1, 2])
        d1.button("📑 Dupliquer ce workflow", on_click=dupliquer_workflow, width="stretch")
        if d2.button("🗑️ Supprimer ce workflow", width="stretch"):
            fenetre_supprimer_workflow()

    # Indicateurs + récapitulatif
    c = compter(wf)
    mini, maxi, boucle = calculer_durees(wf)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📄 Tâches administratives", c["Administratif"]["taches"], border=True)
    m2.metric("🔧 Tâches techniques", c["Technique"]["taches"], border=True)
    m3.metric("🧩 Sous-étapes", sum(v["sous_etapes"] for v in c.values()), border=True)
    m4.metric("⏱️ Durée estimée", (f"{maxi} j" if mini == maxi else f"{mini} à {maxi} j") if wf["taches"] else "—",
              border=True, help="Somme des délais du chemin le plus long. 1 semaine = 7 jours, 1 mois = 30 jours. "
                                "Avec des choix (OU), on affiche la durée la plus courte et la plus longue.")
    if boucle:
        st.info("Ce workflow contient une boucle (retour à une tâche précédente) : "
                "elle n'est comptée qu'une seule fois dans la durée.", icon="🔁")

    st.markdown("#### Récapitulatif par métier")
    st.dataframe(tableau_recap(wf), hide_index=True, width="content")

    # Schéma
    st.markdown("#### Schéma du workflow")
    dot = schema_workflow(wf)
    if dot:
        st.graphviz_chart(dot, width="stretch")
        st.caption("Lecture : 📄 bleu = Administratif, 🔧 orange = Technique (le métier est aussi écrit dans chaque bloc). "
                   "Losange **OU** = un seul chemin selon une condition ; losange **ET** = tâches lancées en même temps. "
                   "Le texte sur les flèches indique le délai.")
    else:
        with st.container(border=True):
            st.markdown("**Ce workflow est vide.** Ajoutez votre première tâche avec le bouton ci-dessous : "
                        "le schéma se dessinera automatiquement.")

    # Liste des tâches
    st.markdown("#### Tâches")
    if st.button("➕ Ajouter une tâche", type="primary"):
        fenetre_ajouter_tache()

    total = len(wf["taches"])
    for i, t in enumerate(wf["taches"], start=1):
        with st.container(border=True):
            gauche, droite = st.columns([3, 2], vertical_alignment="center")
            with gauche:
                nb = len(t["sous_etapes"])
                st.markdown(f"**T{i} · {echapper(t['nom'])}** &nbsp; {badge(t['metier'])} &nbsp; "
                            f"<span style='color:#475569'>{nb} sous-étape{'s' if nb > 1 else ''}</span>",
                            unsafe_allow_html=True)
                st.caption(texte_suite(wf, t))
            with droite:
                b1, b2, b3 = st.columns([1.1, 1.1, 1.3])
                b1.button("🔍 Ouvrir", key=f"z_{t['id']}", on_click=ouvrir_tache, args=(t["id"],),
                          width="stretch", help=f"Voir et modifier la tâche T{i}, ses sous-étapes et ses délais")
                b2.button("▲ Monter", key=f"h_{t['id']}", on_click=deplacer_tache, args=(t["id"], -1),
                          disabled=i == 1, width="stretch", help="Monter la tâche dans la liste")
                b3.button("▼ Descendre", key=f"b_{t['id']}", on_click=deplacer_tache, args=(t["id"], 1),
                          disabled=i == total, width="stretch", help="Descendre la tâche dans la liste")


# ---- Écran 3 : zoom sur une tâche -----------------------------------

def page_tache(wf: dict, t: dict):
    rev = st.session_state["rev"]
    tid = t["id"]
    n = numero_tache(wf, tid)
    st.markdown(f'<div class="fil-ariane">Accueil › {echapper(wf["nom"])} › <strong>Tâche T{n}</strong></div>',
                unsafe_allow_html=True)
    st.title(f"T{n} · {t['nom']}")
    st.markdown(badge(t["metier"]), unsafe_allow_html=True)
    barre_actions(wf, avec_retour_workflow=True)

    # Passer d'une tâche à l'autre sans revenir au workflow
    ids = [x["id"] for x in wf["taches"]]
    p1, p2, _ = st.columns([1, 1, 2])
    p1.button("◀ Tâche précédente", on_click=ouvrir_tache, args=(ids[n - 2],) if n > 1 else (tid,),
              disabled=n == 1, width="stretch")
    p2.button("Tâche suivante ▶", on_click=ouvrir_tache, args=(ids[n],) if n < len(ids) else (tid,),
              disabled=n == len(ids), width="stretch")
    afficher_messages()

    with st.expander("🗺️ Situer cette tâche dans le workflow"):
        st.graphviz_chart(schema_workflow(wf, surligner=tid), width="stretch")
        st.caption("La tâche ouverte est entourée d'un trait épais.")

    onglet_infos, onglet_etapes, onglet_suite = st.tabs(
        ["📝 Description", f"🧩 Sous-étapes ({len(t['sous_etapes'])})", "➡️ Enchaînement et délais"])

    # --- Onglet 1 : nom, métier, note ---
    with onglet_infos:
        st.text_input("Nom de la tâche", value=t["nom"], key=f"t_nom_{tid}_{rev}",
                      on_change=cb_champ_tache, args=(tid, "nom", f"t_nom_{tid}_{rev}"))
        st.radio("Métier", METIERS, index=METIERS.index(t["metier"]), horizontal=True,
                 format_func=lambda m: f"{STYLE_METIER[m]['icone']} {m}",
                 key=f"t_metier_{tid}_{rev}", on_change=cb_champ_tache, args=(tid, "metier", f"t_metier_{tid}_{rev}"))
        st.text_area("Note détaillée", value=t["note"], height=220, key=f"t_note_{tid}_{rev}",
                     placeholder="Décrivez la tâche : qui la fait, avec quels documents, points de vigilance…",
                     help="Enregistrée dès que vous cliquez en dehors du champ. "
                          "Mise en forme possible : **gras**, listes avec « - » en début de ligne.",
                     on_change=cb_champ_tache, args=(tid, "note", f"t_note_{tid}_{rev}"))
        if t["note"].strip():
            with st.expander("Aperçu de la note mise en forme"):
                st.markdown(t["note"])

    # --- Onglet 2 : sous-étapes ---
    with onglet_etapes:
        dot = schema_sous_etapes(t)
        if dot:
            st.graphviz_chart(dot, width="content")
        else:
            st.info("Cette tâche n'a pas encore de sous-étape. Ajoutez-en une ci-dessous "
                    "(ex. : « Saisir la référence fournisseur »).", icon="🧩")

        with st.form(f"form_sous_etape_{tid}", clear_on_submit=True, border=True):
            nom_se = st.text_input("Nouvelle sous-étape", placeholder="ex. : Attribuer un numéro de lot")
            ajouter = st.form_submit_button("➕ Ajouter la sous-étape", type="primary")
        if ajouter:
            if not nom_se.strip():
                st.error("Indiquez le nom de la sous-étape, puis cliquez sur « Ajouter la sous-étape ».")
            else:
                modifier(lambda w: trouver_tache(w, tid)["sous_etapes"].append(nouvelle_sous_etape(nom_se)))
                st.rerun()

        nb = len(t["sous_etapes"])
        for j, s in enumerate(t["sous_etapes"], start=1):
            sid = s["id"]
            with st.expander(f"{n}.{j} · {s['nom']}" + (" 📝" if s["note"].strip() else ""), expanded=False):
                st.text_input("Nom de la sous-étape", value=s["nom"], key=f"s_nom_{sid}_{rev}",
                              on_change=cb_champ_sous_etape, args=(tid, sid, "nom", f"s_nom_{sid}_{rev}"))
                st.text_area("Note", value=s["note"], height=130, key=f"s_note_{sid}_{rev}",
                             placeholder="Détails, documents utiles, points de contrôle…",
                             on_change=cb_champ_sous_etape, args=(tid, sid, "note", f"s_note_{sid}_{rev}"))
                b1, b2, b3 = st.columns(3)
                b1.button("▲ Monter", key=f"sh_{sid}", on_click=deplacer_sous_etape, args=(tid, sid, -1),
                          disabled=j == 1, width="stretch")
                b2.button("▼ Descendre", key=f"sb_{sid}", on_click=deplacer_sous_etape, args=(tid, sid, 1),
                          disabled=j == nb, width="stretch")
                if b3.button("🗑️ Supprimer", key=f"sd_{sid}", width="stretch"):
                    fenetre_supprimer_sous_etape(tid, sid)

    # --- Onglet 3 : liens vers les tâches suivantes, avec délais ---
    with onglet_suite:
        precedentes = [x for x in wf["taches"] if any(l["cible"] == tid for l in x["liens"])]
        if precedentes:
            st.caption("Arrive après : " + " ; ".join(nom_court(wf, x["id"]) for x in precedentes))
        else:
            st.caption("Aucune tâche avant celle-ci : c'est une tâche de départ.")

        st.markdown("**Après cette tâche, on passe à :**")
        if not t["liens"]:
            st.info("Aucune tâche suivante : le workflow se termine ici. "
                    "Ajoutez une tâche suivante si le process continue.", icon="🏁")

        if len(t["liens"]) > 1:
            st.radio("Comment s'enchaînent les tâches suivantes ?", list(SORTIES),
                     index=list(SORTIES).index(t["sortie"]), format_func=SORTIES.get,
                     key=f"t_sortie_{tid}_{rev}", on_change=cb_champ_tache,
                     args=(tid, "sortie", f"t_sortie_{tid}_{rev}"))

        autres = [x["id"] for x in wf["taches"] if x["id"] != tid]
        choix = t["sortie"] == "alternative" and len(t["liens"]) > 1
        for k, l in enumerate(t["liens"], start=1):
            lid = l["id"]
            with st.container(border=True):
                if choix:
                    st.text_input(f"Condition pour le chemin {k}", value=l["condition"], key=f"l_cond_{lid}_{rev}",
                                  placeholder="ex. : Si le lot est conforme",
                                  on_change=cb_champ_lien, args=(tid, lid, "condition", f"l_cond_{lid}_{rev}"))
                c1, c2, c3, c4 = st.columns([3, 1.2, 1.5, 1.3], vertical_alignment="bottom")
                c1.selectbox("Tâche suivante", autres, index=autres.index(l["cible"]),
                             format_func=lambda x: nom_court(wf, x), key=f"l_cible_{lid}_{rev}",
                             on_change=cb_champ_lien, args=(tid, lid, "cible", f"l_cible_{lid}_{rev}"))
                c2.number_input("Délai", min_value=0, max_value=9999, step=1, value=int(l["delai"]),
                                key=f"l_delai_{lid}_{rev}",
                                help="Temps entre la fin de cette tâche et le début de la suivante.",
                                on_change=cb_champ_lien, args=(tid, lid, "delai", f"l_delai_{lid}_{rev}"))
                c3.selectbox("Unité", list(UNITES), index=list(UNITES).index(l["unite"]),
                             format_func=lambda u: UNITES_LIBELLES[u][1], key=f"l_unite_{lid}_{rev}",
                             on_change=cb_champ_lien, args=(tid, lid, "unite", f"l_unite_{lid}_{rev}"))
                c4.button("Retirer", key=f"l_sup_{lid}", on_click=supprimer_lien, args=(tid, lid),
                          width="stretch", help="Retire uniquement la flèche, pas la tâche suivante.")

        st.button("➕ Ajouter une tâche suivante", on_click=ajouter_lien, args=(tid,), type="primary",
                  disabled=not autres,
                  help="Relie cette tâche à une autre tâche du workflow. "
                       "Avec plusieurs tâches suivantes, choisissez ensuite ET (parallèle) ou OU (choix).")
        if not autres:
            st.caption("Créez d'abord une autre tâche dans le workflow pour pouvoir la relier.")

    st.divider()
    if st.button("🗑️ Supprimer cette tâche"):
        fenetre_supprimer_tache(tid)


# =====================================================================
# 11. PROGRAMME PRINCIPAL
# =====================================================================

def initialiser_etat():
    valeurs = {"page": "accueil", "wf": None, "tache_id": None, "historique": {}, "rev": 0,
               "import_n": 0, "import_attente": None, "derniere_sauvegarde": None,
               "alerte": None, "info": None}
    for cle, valeur in valeurs.items():
        st.session_state.setdefault(cle, valeur)


def main():
    st.set_page_config(page_title="Atelier Workflows", page_icon="🗂️", layout="wide")
    initialiser_etat()
    injecter_style()

    workflows, illisibles = lister_workflows()
    barre_laterale(workflows)

    if st.session_state.get("import_attente"):
        fenetre_conflit_import()

    wf = st.session_state.get("wf")
    page = st.session_state["page"]
    if page in ("workflow", "tache") and wf is None:
        page = st.session_state["page"] = "accueil"

    if page == "tache":
        tache = trouver_tache(wf, st.session_state["tache_id"])
        if tache is None:
            st.session_state["page"] = "workflow"
            page_workflow(wf)
        else:
            page_tache(wf, tache)
    elif page == "workflow":
        page_workflow(wf)
    else:
        page_accueil(workflows, illisibles)


if __name__ == "__main__":
    main()
