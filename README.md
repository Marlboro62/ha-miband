<p>
  ☕ <strong>Si cette carte vous est utile, vous pouvez soutenir le projet :</strong>
</p>

<div style="white-space: nowrap; line-height: 0;">
  <a
    href="https://www.buymeacoffee.com/Marlboro62"
    target="_blank"
    rel="noopener noreferrer"
    style="display: inline-block; text-decoration: none; vertical-align: middle;"
  ><img
    src="https://img.buymeacoffee.com/button-api/?text=Offrir%20un%20caf%C3%A9&emoji=%E2%98%95&slug=Marlboro62&button_colour=FF5F5F&font_colour=ffffff&font_family=Cookie&outline_colour=000000&coffee_colour=FFDD00"
    alt="Offrir un café sur Buy Me a Coffee"
    height="45"
    style="display: block; border: 0;"
  ></a>&nbsp;&nbsp;<a
    href="https://ko-fi.com/M4M2QL45X"
    target="_blank"
    rel="noopener noreferrer"
    style="display: inline-block; text-decoration: none; vertical-align: middle;"
  ><img
    src="https://storage.ko-fi.com/cdn/kofi6.png?v=6"
    alt="Soutenir le projet sur Ko-fi"
    height="45"
    style="display: block; border: 0;"
  ></a>
</div>


# Mi Band 7 pour Home Assistant

Intégration Home Assistant qui récupère les données du **Xiaomi Mi Band 7** **directement en Bluetooth**, sans passer par le cloud : batterie, pas, distance, calories, fréquence cardiaque et sommeil. Elle fonctionne avec l'adaptateur Bluetooth de Home Assistant **ou les proxys Bluetooth ESPHome**.

> [!WARNING]
> **Version bêta.** Le protocole Zepp OS a été réimplémenté et testé contre un bracelet simulé, mais pas encore sur tous les firmwares. Testez d'abord sur une instance de test et ouvrez une [issue](https://github.com/Marlboro62/ha-miband/issues) avec les journaux en cas de souci.

## Ce que vous obtenez

| Entité | Détail |
|---|---|
| Batterie, En charge | Niveau en % et état de charge |
| Pas, Distance, Calories | Totaux du jour, remis à zéro par le bracelet à minuit |
| Fréquence cardiaque | Dernière mesure (attribut `measured_at`), minimum, maximum et moyenne du jour |
| Sommeil total, profond, léger, paradoxal, éveil nocturne | Dernière nuit détectée |
| Endormissement, Réveil | Heures de début et de fin de la dernière nuit |
| Endormi, Porté | État au moment de la dernière synchronisation |
| Dernière synchronisation | Fraîcheur des données (diagnostic) |
| Synchroniser maintenant | Bouton pour forcer une synchronisation |

La fréquence cardiaque provient de la **mesure automatique toute la journée** du bracelet : activez la surveillance de la fréquence cardiaque dans les réglages santé du bracelet dans Zepp Life, avec l'intervalle le plus court possible, **avant** de couper Zepp Life.

## Avant de commencer : les contraintes

1. **Le bracelet ne parle qu'à un seul appareil à la fois.** Home Assistant prend la place du téléphone : retirez l'autorisation Bluetooth à Zepp Life (ou désinstallez-la). Le bracelet ne recevra plus les notifications du téléphone.
2. **Ne désappairez jamais le bracelet dans Zepp Life** et ne le réinitialisez pas : sa clé d'authentification serait invalidée (et l'adresse MAC change après une réinitialisation).
3. **Il faut du Bluetooth à portée du bracelet** : adaptateur de Home Assistant ou proxy ESPHome avec les **connexions actives** activées (`bluetooth_proxy: active: true`). Un proxy dans la chambre permet de suivre le sommeil ; quand le bracelet revient à portée, l'historique manquant est rattrapé (jusqu'à 36 h).

## Installation

### Avec HACS

1. HACS → menu ⋮ → **Dépôts personnalisés** → ajoutez `https://github.com/Marlboro62/ha-miband`, catégorie **Intégration**.
2. Installez **Mi Band 7** (cochez « Afficher les versions bêta » si besoin), puis redémarrez Home Assistant.

### Manuelle

Copiez `custom_components/miband` dans le dossier `config/custom_components/` de Home Assistant, puis redémarrez.

## Configuration

Le bracelet est en général **découvert automatiquement** (Paramètres → Appareils et services). Sinon : **Ajouter une intégration** → **Mi Band 7**, puis choisissez le bracelet ou saisissez son adresse MAC.

L'assistant demande ensuite la **clé d'authentification**, de deux façons :

- **Avec le compte Zepp** : e-mail et mot de passe du compte Zepp / Zepp Life auquel le bracelet est appairé. Ils servent seulement à lire la clé sur les serveurs Zepp et **ne sont pas enregistrés**. Les comptes créés avec une connexion Google ou Xiaomi ne fonctionnent pas par ce biais.
- **À la main** : la clé de 32 caractères hexadécimaux, obtenue par exemple avec [huami-token](https://codeberg.org/argrento/huami-token) ou la [méthode décrite par Gadgetbridge](https://gadgetbridge.org/basics/pairing/huami-xiaomi-server/).

Si la clé devient invalide (bracelet réappairé), Home Assistant propose de la ressaisir.

### Options

| Option | Par défaut | Rôle |
|---|---|---|
| Intervalle de synchronisation | 10 min | Délai entre deux connexions (5 à 180 min) |
| Mettre le bracelet à l'heure | Oui | Le téléphone ne le fait plus, Home Assistant s'en charge |

## Fonctionnement

À chaque synchronisation, l'intégration se connecte au bracelet, s'authentifie (échange de clés ECDH sur la courbe B-163 puis chiffrement AES, comme l'appli officielle), lit la batterie et les compteurs du jour, récupère l'**historique minute par minute** depuis la synchronisation précédente, puis **se déconnecte** pour libérer le proxy Bluetooth. Les données sont laissées sur le bracelet.

Le **sommeil** est calculé à partir de cet historique (mêmes seuils que Gadgetbridge pour distinguer léger, profond et paradoxal). Les durées peuvent donc différer légèrement de celles affichées par Zepp Life.

## Dépannage

Activez les journaux détaillés :

```yaml
logger:
  logs:
    custom_components.miband: debug
```

| Message | Cause probable |
|---|---|
| hors de portée de tout adaptateur ou proxy Bluetooth actif | Bracelet trop loin, ou proxy sans connexions actives |
| Synchronisation impossible | Bracelet encore connecté au téléphone (Zepp Life), ou liaison Bluetooth instable |
| Demande de nouvelle clé | Clé refusée : bracelet désappairé ou réinitialisé |

Pour toute question ou problème : [issues GitHub](https://github.com/Marlboro62/ha-miband/issues) uniquement.

## Remerciements et licence

Le protocole Bluetooth Zepp OS est documenté grâce au travail du projet [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) (AGPL-3.0) ; la récupération de clé reprend la séquence de [huami-token](https://codeberg.org/argrento/huami-token) (MIT). Projet sans lien avec Xiaomi, Zepp Health ou Amazfit.

Licence : [GNU AGPL-3.0](LICENSE).
