# Code Signing pour Reachy Mini Desktop App

## Rapport d'analyse des solutions de signature de code Windows

**Date :** 30 décembre 2024  
**Auteur :** Équipe Reachy Mini  
**Projet :** reachy_mini_desktop_app (Reachy Mini Desktop App)

---

## 1. Contexte et problématique

### Pourquoi signer notre application ?

Actuellement, lors du téléchargement de l'application Reachy Mini Desktop sur Windows, les utilisateurs rencontrent :
- ⚠️ **Alertes Windows SmartScreen** effrayantes ("Windows a protégé votre PC")
- 🔴 Avertissements antivirus (faux positifs)
- 📉 Perte de confiance des utilisateurs

La **signature de code** (code signing) résout ces problèmes en :
- Garantissant l'authenticité de l'éditeur
- Prouvant que le code n'a pas été modifié
- Établissant une réputation auprès de Microsoft SmartScreen

### Contrainte technique depuis juin 2023

Les autorités de certification (CA) exigent désormais que les clés privées soient stockées sur **hardware sécurisé** (HSM - Hardware Security Module). Il n'est plus possible de stocker un simple fichier `.pfx` localement.

**Impact :** Nécessité d'utiliser des solutions cloud pour intégrer la signature dans notre pipeline CI/CD GitHub Actions.

---

## 2. Solutions analysées

### Tableau comparatif

| Solution | Prix annuel | Intégration CI/CD | SmartScreen | Complexité |
|----------|-------------|-------------------|-------------|------------|
| **SSL.com eSigner** | ~$300-400 | ⭐⭐⭐⭐⭐ Excellente | Réputation à construire | Faible |
| **Azure Trusted Signing** | ~$120 | ⭐⭐⭐⭐⭐ Excellente | Immédiate ✅ | Faible |
| **DigiCert KeyLocker** | ~$500-700 | ⭐⭐⭐⭐ Très bonne | Réputation à construire | Moyenne |
| **Azure Key Vault + certificat** | ~$250-400 | ⭐⭐⭐ Bonne | Réputation à construire | Élevée |

---

## 3. Analyse détaillée des solutions recommandées

### Option A : SSL.com eSigner (Recommandée)

**Présentation :**  
SSL.com est une autorité de certification reconnue proposant eSigner, une solution cloud permettant de signer du code sans token hardware physique.

**Avantages :**
- ✅ GitHub Action officielle disponible
- ✅ Aucun hardware requis
- ✅ Support des certificats EV (Extended Validation) si besoin
- ✅ Documentation complète
- ✅ Prix compétitif

**Inconvénients :**
- ⚠️ Réputation SmartScreen à construire progressivement
- ⚠️ Frais mensuels en plus du certificat (~$20/mois pour eSigner)

**Coût estimé :** ~$300-400/an (certificat OV + eSigner)

**Intégration GitHub Actions :**
```yaml
- name: Sign Windows executable
  uses: sslcom/esigner-codesign@main
  with:
    command: sign
    username: ${{ secrets.ES_USERNAME }}
    password: ${{ secrets.ES_PASSWORD }}
    credential_id: ${{ secrets.CREDENTIAL_ID }}
    totp_secret: ${{ secrets.ES_TOTP_SECRET }}
    file_path: ./target/release/*.exe
```

---

### Option B : Azure Trusted Signing (Alternative économique)

**Présentation :**  
Nouveau service Microsoft (2024) permettant de signer du code via Azure, avec réputation SmartScreen immédiate car validé par Microsoft.

**Avantages :**
- ✅ Prix très compétitif (~$9.99/mois)
- ✅ Réputation SmartScreen immédiate (service Microsoft)
- ✅ Intégration native avec GitHub Actions
- ✅ Pas besoin d'acheter un certificat séparément

**Inconvénients :**
- ⚠️ Service relativement nouveau (moins de retours d'expérience)
- ⚠️ Nécessite un compte Azure
- ⚠️ Processus de validation d'identité entreprise requis

**Coût estimé :** ~$120/an

**Intégration GitHub Actions :**
```yaml
- name: Sign with Azure Trusted Signing
  uses: azure/trusted-signing-action@v0.5.1
  with:
    azure-tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    azure-client-id: ${{ secrets.AZURE_CLIENT_ID }}
    azure-client-secret: ${{ secrets.AZURE_CLIENT_SECRET }}
    endpoint: https://eus.codesigning.azure.net/
    trusted-signing-account-name: reachy-mini
    certificate-profile-name: reachy-mini-profile
    files-folder: ${{ github.workspace }}/target/release
    files-folder-filter: exe,msi
```

---

### Option C : DigiCert KeyLocker (Premium)

**Présentation :**  
DigiCert est le leader mondial des certificats numériques. KeyLocker est leur solution cloud pour le code signing en CI/CD.

**Avantages :**
- ✅ Leader du marché, maximum de confiance
- ✅ Support technique premium
- ✅ GitHub Action disponible

**Inconvénients :**
- ❌ Prix élevé (~$500-700/an)
- ⚠️ Interface parfois complexe

**Coût estimé :** ~$500-700/an

---

## 4. Recommandation

### Pour Reachy Mini Desktop App, nous recommandons :

| Priorité | Solution | Raison |
|----------|----------|--------|
| **1er choix** | Azure Trusted Signing | Meilleur rapport qualité/prix, réputation SmartScreen immédiate |
| **2ème choix** | SSL.com eSigner | Plus établi, bonne documentation, prix raisonnable |

### Justification Azure Trusted Signing :
1. **Coût** : ~$120/an vs ~$300-400 pour les alternatives
2. **SmartScreen** : Réputation immédiate car service Microsoft natif
3. **Intégration** : GitHub Action officielle maintenue par Microsoft
4. **Simplicité** : Pas de gestion de certificat externe

---

## 5. Plan d'implémentation proposé

### Phase 1 : Setup (1-2 jours)
1. Créer un compte Azure (si pas existant)
2. S'inscrire à Azure Trusted Signing
3. Compléter la validation d'identité entreprise
4. Créer le profil de certificat

### Phase 2 : Intégration CI/CD (1 jour)
1. Configurer les secrets GitHub :
   - `AZURE_TENANT_ID`
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
2. Modifier le workflow GitHub Actions
3. Tester sur une branche feature

### Phase 3 : Déploiement (1 jour)
1. Merger sur develop
2. Créer une release de test
3. Valider le fonctionnement sur Windows
4. Déployer en production

**Durée totale estimée :** 3-4 jours

---

## 6. Budget récapitulatif

| Poste | Coût annuel |
|-------|-------------|
| Azure Trusted Signing (Basic) | ~$120 |
| **Total** | **~$120/an** |

*Alternative SSL.com : ~$300-400/an*

---

## 7. Bénéfices attendus

1. **Expérience utilisateur améliorée** : Plus d'alertes SmartScreen effrayantes
2. **Confiance renforcée** : Les utilisateurs voient "Pollen Robotics" comme éditeur vérifié
3. **Réduction du support** : Moins de tickets liés aux faux positifs antivirus
4. **Image professionnelle** : Application signée = standard professionnel

---

## 8. Ressources

- [Azure Trusted Signing Documentation](https://learn.microsoft.com/en-us/azure/trusted-signing/)
- [GitHub Action Azure Trusted Signing](https://github.com/marketplace/actions/trusted-signing)
- [SSL.com eSigner Documentation](https://www.ssl.com/guide/automate-code-signing/)
- [DigiCert Code Signing](https://www.digicert.com/signing/code-signing-certificates)

---

## 9. Conclusion

La signature de code est devenue indispensable pour distribuer des applications Windows professionnelles. Parmi les solutions analysées, **Azure Trusted Signing** offre le meilleur compromis entre coût, simplicité d'intégration et bénéfices immédiats (réputation SmartScreen).

L'investissement de ~$120/an est minimal comparé aux bénéfices en termes d'expérience utilisateur et d'image professionnelle.

---

*Rapport généré le 30 décembre 2024*

