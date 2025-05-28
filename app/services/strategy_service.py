# app/services/strategy_service.py

# # est-ce que la strategie est active (checker dans firestore)
# si oui, est-ce que nous sommes mardi, mercredim jeudi ou vended ?
# # si oui, est-ce qu'il existe des bougies 1m pour le jour en cours ?
# # si oui, est-ce que le nombre de bougies est = à 15 ?
# # si oui, fixer le plus haut et le plus bas du jours dans une nouvelle collection (jour, haut bas)
# # a chaque nouvelle bougie 1m, verifier si le plus haut ou le plus bas est depassé
# # si oui, on envoie un signal d'achat ou de vente à OANDA 
# # a 11h30, on arrete les ordres et on ferme les positions ouvertes
# # # # # # 
# # # # 
# # 
# # 
# # 
# # 
# # 
# # 