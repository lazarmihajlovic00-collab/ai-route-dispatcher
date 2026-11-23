# Vodič za intervju: AI Dispečer ruta

Ovaj dokument je za tebe, da projekat možeš da objasniš i braniš na intervjuu. Pročitaj ga jednom celog, pa uradi
vežbe na kraju.

## 1. Priča za 30 sekundi

"Distributeri pića svako jutro planiraju rute za desetine kamiona: kapaciteti po težini i paletama, kafići koji
primaju robu samo pre otvaranja, centar Beograda gde ne sme kamion od 12 tona, povratna ambalaža koja zauzima mesto.
Napravio sam planer koji to rešava optimizatorom Google OR-Tools, i AI dispečera iznad njega. Dispečer napiše
'kamion 3 je u kvaru' i za par sekundi dobije novi plan: ko preuzima koje isporuke, ko će kasniti i koliko, i koliko
to košta. Čovek odobrava svaku promenu. Na 8 sintetičkih dana optimizovan plan je u proseku 12,1% jeftiniji od
plana po teritorijama, a kasnih isporuka ima 0 umesto 23 dnevno."

## 2. Kako da opišeš svoju ulogu (iskreno)

"Ja sam vlasnik projekta: izabrao sam problem i obim, postavio ciljeve i prihvatio arhitektonske odluke. Kod je
pisan uz AI asistenta kao buildera. Poenta je da znam kako da se takav sistem postavi i orkestrira, i da razumem i
branim svaku odluku." Odluke su u `DECISIONS.md`. Nauči D2, D3, D4, D5 i D9.

## 3. Problem koji se rešava (VRP)

Ovo je **Vehicle Routing Problem sa vremenskim prozorima i kapacitetima** (CVRPTW), uz dodatke iz prakse:

| Ograničenje | Kako je modelovano |
|---|---|
| Težina robe | dimenzija `kg`: zbir isporuka u turi ≤ nosivost vozila |
| Paletna mesta i povratna ambalaža | dimenzija `onboard`: u vozilu posle svake stanice = utovar − isporučeno + preuzeta ambalaža, uvek između 0 i kapaciteta |
| Vremenski prozori kupaca | dimenzija `time`: dolazak između početka i kraja prozora; čekanje do 4 h dozvoljeno |
| Smena | najraniji polazak 06:00, povratak do 16:00, najviše 10 h |
| Zone | centar Beograda do 7,5 t, neke ulice samo kombi (`VehicleVar.RemoveValues`) |
| Troškovi | po km (zavisi od vozila), posada po satu (span cost), fiksno po korišćenom vozilu |
| Porudžbina koja ne staje | može da se izostavi samo uz ogromnu kaznu, pa solver kaže šta ne staje umesto da padne |

Kako OR-Tools radi, u dve rečenice: prvo napravi početno rešenje ubacivanjem stanica tamo gde najmanje koštaju
(*parallel cheapest insertion*), pa ga popravlja lokalnom pretragom (premeštanje, zamena stanica između ruta) uz
*guided local search*, koji kažnjava "loše" grane da ne bi zaglavio u lokalnom minimumu. Radi dok ne istekne
vremenski limit (10 s za ceo dan).

Povratna ambalaža je fora koju treba da znaš da objasniš: kamion polazi pun, na svakoj stanici istovari pa utovari
prazne gajbe i burad. Ako prvo ode kod kupca koji vraća mnogo ambalaže, može da ostane bez mesta. Test
`test_returnable_empties_force_delivery_first` pokazuje da solver zbog toga menja redosled.

## 4. Šta se dešava kad dispečer napiše "Kamion 1 je u kvaru"

1. **Razumevanje.** Sa ključem, AI model (Gemini ili Claude) dobije rečenicu, vreme i listu alata
   (`pronadji_kupca`, `predlozi_kvar`, `predlozi_hitnu_porudzbinu`, ...). Izabere alat i argumente. Bez ključa isto
   radi parser sa pravilima.
2. **Ponovno planiranje** (`replan.py`):
   - pre polaska: ceo dan iz početka, ali sa kaznom 400 RSD za svaku stanicu koja promeni vozilo (D6);
   - u toku dana: isporučeno ostaje, kamioni na putu zadržavaju robu i redosled, a otvorene isporuke idu u
     **dodatne ture** iz baze, vozilima koja nisu krenula ili posle povratka i pretovara (30 min). Prozori postaju
     "meki": kašnjenje košta 150 RSD po minutu, najviše 2 h (D4).
3. **Simulacija.** Novi plan prolazi kroz isti simulator kao i svaki drugi: vremena, opterećenje, troškovi,
   prekršaji.
4. **Predlog.** Pravi se razlika (diff): ko je premešten, ko kasni, šta danas ne može, promena troška. AI napiše
   objašnjenje samo iz tih brojeva.
5. **Odobrenje.** Plan se ne menja dok imenovana osoba ne klikne "Primeni plan". Agent ne može da odobri. Ako se
   plan u međuvremenu promenio, predlog je zastareo. Sve ide u audit log sa lancem heševa.

## 5. Brojevi koje treba da znaš

Iz `docs/eval_report.md` (sintetički podaci, 95 porudžbina dnevno, 9 vozila):

- Optimizovan plan vs plan "po teritorijama": **12,1%** niži trošak u proseku (5,5% do 17,7%), oko
  28.000 RSD dnevno, 134 km manje.
- Kasne isporuke: 23 dnevno u ručnom planu, **0** u optimizovanom.
- Svi planovi prolaze proveru ograničenja (0 prekršaja).
- Kvarovi u toku dana: 93,8% otvorenih isporuka isporučeno isti dan, 39,8% i u prozoru. Novi plan za oko
  5 s (to je vremenski limit solvera).
- Komande na srpskom: parser sa pravilima 66,7% na 33 komande. Najteže su "dvojka je u kvaru", "4 gajbe i
  1 bure", "pomeri na 11-13" i dva kupca sa sličnim imenom. Gemini 3.5 Flash Lite (besplatan nivo) tačno razume
  **93,9%** (31 od 33), oko 4,7 s po komandi. Promašio je dva kupca sa sličnim imenom i "može tek od 13 do 15h", gde je
  tražio pojašnjenje umesto da promeni termin. Ako pitaju: kad model upiše trenutno vreme kao vreme kvara, to je isto
  što i "sada", pa se računa kao tačno; strogo poređenje daje 78,8%.
- Biznis slučaj (`roi.py`): ušteda na rutama + vreme dispečera (pretpostavke: 90 min ručnog planiranja dnevno,
  35 min po poremećaju, 1,5 poremećaja dnevno). Brojevi se menjaju u dashboardu.

Budi iskren o ograničenjima: podaci su sintetički, ručni plan je heuristika a ne pravi plan firme, a razdaljine
su vazdušna linija × 1,32 dok se ne uključi OSRM. Pravi broj daje pilot: istorijski planovi firme za mesec dana,
pa poređenje na istim porudžbinama.

## 6. Pitanja i odgovori

**Zašto ne pustiš ChatGPT da napravi rute?**
Zato što je rutiranje kombinatorni problem: 95 stanica i 9 vozila imaju astronomski broj kombinacija, a LLM ne
može ni da proveri da li je plan izvodljiv. Model koristim za ono što radi dobro: da razume rečenicu dispečera i
objasni rezultat. Rute računa solver, brojeve simulator.

**Da li je rešenje optimalno?**
Ne garantovano. OR-Tools sa lokalnom pretragom daje dobro rešenje u zadatom vremenu, ne dokaz optimalnosti. Za
dispečera je bitnije da dobije dobar plan za 10 sekundi nego optimalan za sat vremena. Zato upoređujem sa baseline
planom i prijavljujem vremenski limit.

**Zašto na danu 1 optimizovan plan koristi 9 vozila, a ručni 8, a ipak je jeftiniji?**
Jer je fiksni trošak vozila samo deo priče. Ručni plan sa 8 vozila ima duže ture, više sati posade i 15 kasnih
isporuka. Solver je izračunao da je jedno vozilo više jeftinije od prekovremenih sati i kašnjenja. Ako firma ima
drugačiji fiksni trošak, menja se jedan broj i solver to uračuna.

**Šta ako kamion pukne u 10:30 i ima robu za 6 kupaca?**
Isporučeno ostaje. Ostali kamioni nastavljaju svojim redom, jer je roba već utovarena. Šest otvorenih isporuka ide
u dodatne ture: kombiji koji su se vratili u bazu se pretovaruju i kreću ponovo. Ako neko kasni, plan kaže ko i
koliko, da dispečer pozove kupca. Ako nešto ne može danas, kaže zašto.

**Kako znaš da plan poštuje ograničenja?**
Simulator posle svakog planiranja ponovo proveri sve: težinu, palete posle svake stanice, prozore, zone, smenu.
Testovi to proveravaju i nezavisno, a evaluacija meri broj prekršaja na svakom planu (0).

**Zašto Gemini, a ne Claude?** (ili obrnuto)
Sistem podržava oba kroz jedno podešavanje. Gemini ima besplatan nivo, što je dobro za demo. Za produkciju bih
izabrao model po tačnosti na evaluaciji komandi i ceni po komandi, oba broja meri `dispatcher eval --llm`.

**Šta bi uradio za pravu firmu?**
(1) Pravi putni podaci iz OSRM-a ili Google/HERE matrice sa saobraćajem po satima. (2) Uvoz porudžbina iz ERP-a
(Pantheon, SAP). (3) Mobilna aplikacija za vozače sa redosledom i potvrdom isporuke, da sistem zna gde je ko u
realnom vremenu. (4) Pauze vozača po propisima. (5) Pilot od mesec dana: poređenje sa stvarnim planovima.

## 7. Vežbe pre intervjua

1. Pokreni `dispatcher seed`, pa `dispatcher serve`, otvori dashboard. Pomeri klizač vremena i gledaj kamione.
2. Napiši "Kamion 1 je u kvaru". Pročitaj predlog i nađi u vremenskoj liniji dodatne ture.
3. Otvori `dispatcher/solver.py` i nađi gde je modelovana povratna ambalaža (dimenzija `onboard`).
4. Pročitaj `replan.py`, funkciju `_intraday`. Objasni naglas tri pravila iz D4.
5. Promeni u `data.py` fiksni trošak kamiona na 20.000 RSD, pokreni `dispatcher eval --seeds 1 --seconds 5` i
   objasni zašto se promenio broj vozila.
6. Objasni nekome u 2 minuta zašto LLM ne pravi rute (D2).
