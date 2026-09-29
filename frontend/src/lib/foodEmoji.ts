/*
 * An emoji for an ingredient line, from its words.
 *
 * Organised by family; within the list, specific terms come before general ones and the first
 * match wins, so "curry leaves" is a leaf 🌿 before "curry" is a spice jar 🫙, "egg noodles" is
 * pasta 🍝 before "egg" is an egg 🥚, "tomato paste" is a can 🥫 before "tomato" is a tomato 🍅,
 * and "chicken stock" is a pot 🍲 before "chicken" is a chicken 🐔.
 *
 * Meaning of the pantry marks (there is no emoji for most dry goods):
 *   🧂 salt (and salt-and-pepper)        🫙 a jar: ground spices, blends, pastes, condiments
 *   🍂 dried herbs and leaves            🌿 fresh herbs and leaves
 *   🍶 a bottle: vinegar, soy/fish sauce, mirin, extracts
 *   🌻 seeds and seed oils               🫒 olives and olive oil
 * Lines that are headings, not ingredients ("Toppings", "Garnish", "For serving") get nothing.
 *
 * Checked against every ingredient line in the household catalog.
 */

const RULES: [RegExp, string][] = [
  // ---- not an ingredient: section headings and instructions that slipped into the list ----
  [/^[\s.\-–]*(toppings?|garnish(es)?|decorations?|fillings?|condiments|dressing|salad|sandwiches|tacos|wraps|bowls|optional|print|sheet pan|x 2x 3x|for (the|serving)|to serve|finely sliced|shredded|grated|peeled and cut)\b(\s*(as desired|for \d+|\(optional\)|see note|toppings, see note))?[^a-z]*$/, ""],
  [/^[\s.\-–]*(ingredients? (were not|[a-z ]*salad$)|measuring cup|mix together|cook under)/, ""],
  // salt first when the line is about salt, so "kosher salt (for a larger filet)" is salt
  [/^[^a-z]*(kosher |sea |table |fine |flaky |flakey |coarse |pink |himalayan |diamond crystal |morton )*salt\b|\bsalt and (black |freshly ground |fresh(ly)? cracked )?pepper\b|\bsalt & pepper\b/, "🧂"],

  // ---- stocks and soups (before the meats and vegetables named inside them) ----
  [/\b(stock|broth|consomm[eé]|bouillon|dashi)\b/, "🍲"],

  // ---- dairy and eggs (noodles before eggs) ----
  [/\bnoodles?\b|\bwonton wrappers?\b|\bdumpling wrappers?\b/, "🍝"],
  [/\bcream cheese\b|\bmascarpone\b|\bricotta\b|\bcottage cheese\b|\bqueso\b|\bpaneer\b|\bhalloumi\b|\bburrata\b|\bcotija\b/, "🧀"],
  [/\bcheeses?\b|\bparmigiano|\bparmesan|\breggiano|\bcheddar\b|\bmozzarella\b|\bfeta\b|\bgruy[eè]re\b|\bpecorino\b|\bromano\b(?! dressing)|\bmanchego\b|\bgorgonzola\b|\broquefort\b|\bstilton\b|\bbrie\b|\bcamembert\b|\bprovolone\b|\bfontina\b|\basiago\b|\bmonterey jack\b|\bpepper jack\b|\bmuenster\b|\bhavarti\b|\bcolby\b|\bvelveeta\b|\bemmental\b/, "🧀"],
  [/\bbuttermilk\b/, "🥛"],
  [/\bbutter\b|\bghee\b|\bmargarine\b|\bshortening\b|\bbaking sticks?\b/, "🧈"],
  [/\bcondensed milk\b|\bevaporated milk\b/, "🥫"],
  [/\bcoconut milk\b|\bcoconut cream\b|\bcream of coconut\b/, "🥥"],
  [/\bice cream\b|\bgelato\b|\bsorbet\b|\bfrozen yog(h)?urt\b/, "🍨"],
  [/\byog(h)?urt\b|\bkefir\b|\blabneh\b|\bskyr\b|\btzatziki\b|\braita\b/, "🥣"],
  [/\b(heavy|whipping|double|single|sour|light|clotted|whipped) cream\b|\bcr[eè]me fra[iî]che\b|\bhalf[- ]and[- ]half\b|\bcream\b(?! of tartar)/, "🥛"],
  [/\bmilk\b/, "🥛"],
  [/\beggs?\b|\byolks?\b|\begg whites?\b/, "🥚"],

  // ---- fauna ----
  [/\bbacon\b|\bpancetta\b|\bguanciale\b|\blardons?\b/, "🥓"],
  [/\bsausages?\b|\bchorizo\b|\bbratwurst\b|\bkielbasa\b|\bandouille\b|\bhot dogs?\b|\bfrankfurter/, "🌭"],
  [/\bchicken\b|\bpoultry\b|\bhen\b|\bpoussin\b|\bcornish\b|\bdrumsticks?\b|\bwings\b/, "🐔"],
  [/\bham\b|\bprosciutto\b|\bpork ribs\b|\bspare ?ribs\b|\bbaby back\b|\bpork\b|\blard\b|\bspam\b|\bsalami\b|\bpepperoni\b|\bmortadella\b/, "🐖"],
  [/\banchov(y|ies)\b|\bsardines?\b|\bsalmon\b|\btuna\b|\bcod\b|\bhalibut\b|\btrout\b|\btilapia\b|\bsnapper\b|\bmackerel\b|\bmarlin\b|\bswordfish\b|\bhaddock\b|\bsea ?bass\b|\bbass\b|\bcatfish\b|\bmahi\b|\bfish\b(?! sauce)|\blox\b|\bcaviar\b|\broe\b/, "🐟"],
  [/\bsteaks?\b|\brib ?eye\b|\bsirloin\b|\bfilet\b|\bmignon\b|\bt-bone\b|\bstrip loin\b|\bflank\b|\bskirt\b|\bhanger\b|\bflat iron\b|\bprime rib\b|\brib roast\b/, "🥩"],
  [/\bbeef\b|\bbrisket\b|\bchuck\b|\bground round\b|\bshort ribs?\b|\boxtail\b|\bstew meat\b|\bveal\b|\blengua\b|\btongue\b|\bbone marrow\b|\bmarrow bones?\b|\bmarrow\b|\brag[uù]\b|\bbolognese\b|\boxtails?\b/, "🐄"],
  [/\bturkey\b/, "🦃"],
  [/\bduck\b|\bgoose\b/, "🦆"],
  [/\blamb\b|\bmutton\b/, "🐑"],
  [/\bgoat\b/, "🐐"],
  [/\bvenison\b|\bdeer\b|\belk\b/, "🦌"],
  [/\bbison\b|\bbuffalo\b/, "🦬"],
  [/\brabbit\b|\bhare\b/, "🐇"],
  [/\bchops?\b/, "🐖"],
  [/\bkefta\b|\bkofta\b|\bmeatballs?\b|\bground meat\b|\bmeat\b/, "🥩"],
  [/\bprotein powder\b|\bwhey\b/, "🥤"],
  [/\bshrimp\b|\bprawns?\b/, "🦐"],
  [/\bcrab\b|\bcrabmeat\b/, "🦀"],
  [/\blobster\b|\bcrayfish\b|\bcrawfish\b|\blangoustine/, "🦞"],
  [/\bsquid\b|\bcalamari\b|\boctopus\b|\bcuttlefish\b/, "🦑"],
  [/\bscallops?\b/, "🐚"],
  [/\bclams?\b|\bmussels?\b|\boysters?\b(?! sauce)|\bcockles?\b/, "🦪"],
  [/\bbones?\b|\bbone-in\b/, "🦴"],

  // ---- flora: herbs and leaves (fresh vs dried), then the spice jar ----
  [/\bcurry leaves\b|\b(kaffir |makrut )?lime leaves\b|\bbanana leaves\b|\bgrape leaves\b|\bshiso\b|\bperilla\b|\bpandan\b/, "🌿"],
  [/\bgarlic powder\b/, "🫙"],
  [/\bgarlic\b/, "🧄"],
  [/\bcucumbers?\b|\bcukes?\b|\bpickles?\b|\bgherkins?\b|\bcornichons?\b|\bzucchini|\bcourgette|\bsummer squash/, "🥒"],
  [/\blettuce\b|\bromaine\b/, "🥬"],
  [/(?<!fresh.*)\bdried (basil|oregano|thyme|rosemary|parsley|dill|mint|sage|marjoram|tarragon|herbs?|chives|cilantro)\b|\bbay lea(f|ves)\b|\bherbes de provence\b|\bitalian seasoning\b|\bza'?atar\b/, "🍂"],
  [/\bcoriander (leaves|leaf)\b|\bfresh coriander\b/, "🌿"],
  [/\bcoriander\b|\bcumin\b|\bfennel seeds?\b|\bcelery (seeds?|salt)\b|\bcaraway\b|\bmustard seeds?\b|\bfenugreek\b|\bnigella\b|\bcardamom\b|\bcinnamon\b|\bnutmeg\b|\ballspice\b|\bstar anise\b|\banise\b|\b(?<!garlic )cloves?\b(?! of garlic| garlic)|\bmace\b|\bsaffron\b|\bsumac\b|\bmasala\b|\bcurry\b|\bfive[- ]spice\b|\bbaharat\b|\bras el hanout\b|\bberbere\b|\bdukkah\b|\bpumpkin pie spice\b|\bpoultry seasoning\b|\bold bay\b|\bseasoning\b|\bspice (blend|mix|rub)\b|\bspices?\b|\brub\b|\bbouquet garni\b|\bground ginger\b|\bginger powder\b|\bonion powder\b|\bgarlic powder\b|\bturmeric powder\b|\bground turmeric\b|\bpaprika\b|\bcayenne\b|\bchil[ie] powder\b|\bchipotle powder\b|\bancho powder\b|\bmsg\b|\bliquid smoke\b|\bhickory smoke\b|\bprague powder\b|\bcuring salt\b|\bkala namak\b|\bblack salt\b|\btamarind\b|\bmiso\b|\bgochujang\b|\bdoenjang\b|\bharissa\b|\bsambal\b|\bchil[ie] (garlic )?paste\b|\baj[ií] (amarillo|panca)\b|\bmarmite\b|\bvegemite\b|\bbouillon cubes?\b|\bstock cubes?\b|\brosewater\b|\borange blossom\b/, "🫙"],
  [/\bbasil\b|\bparsley\b|\bcilantro\b|\bmint\b|\bdill\b|\bthyme\b|\brosemary\b|\boregano\b|\bsage\b|\btarragon\b|\bmarjoram\b|\bchervil\b|\bchives\b|\blemongrass\b|\blemon grass\b|\bherbs?\b|\bsorrel\b|\bwatercress\b|\bmicrogreens\b|\bsprigs?\b|\bleaves\b|\bpesto\b|\bchimichurri\b|\bsalsa verde\b|\bgremolata\b|\bepazote\b|\bhoja santa\b/, "🌿"],

  // ---- flora: the aromatic base ----
  [/\bonions?\b|\bshallots?\b|\bscallions?\b|\bgreen onions?\b|\bspring onions?\b|\bleeks?\b|\bramps?\b/, "🧅"],
  [/\bgalangal\b|\bginger\b|\bturmeric\b/, "🫚"],
  [/\bjalape[nñ]os?\b|\bserranos?\b|\bhabaneros?\b|\bchipotles?\b|\bpoblanos?\b|\banaheim\b|\bfresno\b|\bthai chil|\bbird'?s eye\b|\bscotch bonnet\b|\bchil(i|e|li|ly)e?s?\b|\bchillies\b|\bred pepper flakes\b|\bcrushed red pepper\b|\bpepper flakes\b|\bhot sauce\b|\bsriracha\b|\btabasco\b|\bpepperoncini\b|\bpimient[oa]s?\b|\bcalabrian\b|\bpiri[- ]piri\b|\bpiquin\b/, "🌶️"],
  [/\b(black|white|ground|cracked|freshly ground|freshly cracked|coarse(ly)? ground) pepper\b|\bpeppercorns?\b/, "🧂"],
  [/\bbell peppers?\b|\bcapsicum\b|\b(red|green|yellow|orange|sweet|roasted|baby) peppers?\b|\bpeppers\b/, "🫑"],

  // ---- flora: vegetables ----
  [/\btomato (paste|pur[eé]e|sauce|passata)\b|\bpassata\b|\bmarinara\b|\bpizza sauce\b|\bcrushed tomatoes\b|\bdiced tomatoes\b|\bwhole (peeled )?tomatoes\b|\bcanned tomatoes\b|\bcan(ned)? [^,]*tomatoes\b|\brotel\b/, "🥫"],
  [/\bsalsa\b|\bpico de gallo\b|\btomato|\btomatillo/, "🍅"],
  [/\bsweet potato|\byams?\b/, "🍠"],
  [/\bpotato|\bfries\b|\bhash browns?\b|\btater/, "🥔"],
  [/\bcarrots?\b/, "🥕"],
  [/\bcorn ?starch\b|\bcorn syrup\b|\bcornmeal\b|\bpolenta\b|\bgrits\b|\bhominy\b|\bmasa\b|\bcorn\b|\bmaize\b/, "🌽"],
  [/\bbroccoli|\bbroccolini|\bcauliflower|\bromanesco/, "🥦"],
  [/\bcabbage|\bsauerkraut|\bkimchi|\bbok choy|\bpak choi|\bbrussels?\b|\bcoleslaw|\bslaw\b/, "🥬"],
  [/\bzucchini|\bcourgette|\bsummer squash/, "🥒"],
  [/\barugula|\barugula|\brocket\b|\bspinach|\bkale\b|\bchard\b|\bcollards?\b|\bendive|\bradicchio|\bescarole|\bfris[eé]e|\bmixed greens|\bsalad greens|\bgreens\b|\bcelery\b|\basparagus|\bartichokes?\b|\bfennel\b/, "🥬"],
  [/\bbutternut|\bpumpkin(?! pie spice| seeds?)|\bacorn squash|\bkabocha|\bdelicata|\bsquash\b/, "🎃"],
  [/\bmushrooms?\b|\bshiitake|\bportobello|\bcremini|\bcrimini|\bporcini|\bchanterelle|\boyster mushroom|\benoki|\bmorels?\b|\btruffle/, "🍄"],
  [/\beggplant|\baubergine/, "🍆"],
  [/\bgreen beans?\b|\bstring beans?\b|\bharicots? verts?\b|\bsnap peas|\bsnow peas|\bpeas\b|\bedamame\b|\bokra\b|\bpea\b/, "🫛"],
  [/\bchickpeas?\b|\bgarbanzo|\blentils?\b|\bdal\b|\bdahl\b|\burad\b|\bmoong\b|\bmung\b|\bblack[- ]eyed|\bkidney beans?\b|\bblack beans?\b|\bpinto|\bcannellini|\bnavy beans?\b|\bwhite beans?\b|\bbeans?\b|\btofu\b|\btempeh\b|\bseitan\b|\bsoy curls\b|\bhummus\b/, "🫘"],
  [/\bbeets?\b|\bbeetroot|\bradish|\bturnip|\bdaikon|\bparsnip|\brutabaga|\bcelery root|\bceleriac|\bjicama|\bkohlrabi/, "🥔"],
  [/\bavocados?\b|\bguacamole\b/, "🥑"],
  [/\bolive oil\b|\bolives?\b/, "🫒"],
  [/\bcapers?\b/, "🫙"],
  [/\bsprouts?\b|\bbean sprouts\b/, "🌱"],
  [/\bseaweed|\bnori\b|\bkombu\b|\bwakame\b|\bkelp\b/, "🌊"],

  // ---- flora: fruit ----
  [/\blemons?\b|\blemon (juice|zest|peel)\b/, "🍋"],
  [/\blimes?\b|\blime (juice|zest)\b/, "🍋‍🟩"],
  [/\boranges?\b|\bclementine|\bmandarin|\btangerine|\bgrapefruit|\bkumquat/, "🍊"],
  [/\bapples?\b|\bapple ?sauce\b/, "🍎"],
  [/\bpears?\b/, "🍐"],
  [/\bpeach|\bnectarine|\bapricot|\bplums?\b/, "🍑"],
  [/\bcherr(y|ies)\b/, "🍒"],
  [/\bstrawberr/, "🍓"],
  [/\bblueberr|\bblackberr|\braspberr|\bcranberr|\bcurrants?\b|\bberr(y|ies)\b|\bgooseberr|\bfigs?\b|\belderberr|\bprunes?\b|\bdried fruit\b/, "🫐"],
  [/\bgrapes?\b|\braisins?\b|\bsultanas?\b/, "🍇"],
  [/\bwatermelon/, "🍉"],
  [/\bmelon|\bcantaloupe|\bhoneydew/, "🍈"],
  [/\bbananas?\b|\bplantains?\b/, "🍌"],
  [/\bpineapple/, "🍍"],
  [/\bmango/, "🥭"],
  [/\bkiwi/, "🥝"],
  [/\bcoconut/, "🥥"],
  [/\bdates?\b/, "🌴"],
  [/\bpomegranate/, "🍎"],
  [/\brhubarb\b/, "🥬"],

  // ---- grains, bread, pasta ----
  [/\bbread flour\b|\bbread floor\b/, "🌾"],
  [/\bbread ?crumbs?\b|\bpanko\b|\bcroutons?\b/, "🍞"],
  [/\bbaguette|\bfrench bread\b/, "🥖"],
  [/\bcroissants?\b|\bpuff pastry\b|\bpastry\b|\bphyllo\b|\bfilo\b|\bpie crust\b|\bpie dough\b|\btart dough\b|\bdough\b|\bcrust\b/, "🥐"],
  [/\bbagels?\b/, "🥯"],
  [/\bpretzels?\b/, "🥨"],
  [/\bpancakes?\b|\bwaffles?\b/, "🥞"],
  [/\bbread\b|\bbrioche|\bsourdough|\bbuns?\b|\brolls?\b(?! oats)|\bciabatta|\bfocaccia|\btoast\b|\benglish muffins?\b|\bmuffins?\b|\bbiscuits?\b|\bcornbread\b|\bchallah\b|\bhoagie\b/, "🍞"],
  [/\btortillas?\b|\bwraps?\b|\bflatbreads?\b|\bpitas?\b|\bnaan\b|\broti\b|\bchapati\b|\bpappadum|\bpapadum|\blavash\b|\btostadas?\b|\btaco shells?\b|\btacos?\b/, "🫓"],
  [/\bpasta\b|\bspaghetti|\bpenne\b|\bfet+uc+in[ei]\b|\blinguine\b|\bmacaroni\b|\brigatoni\b|\borzo\b|\blasagn|\bgnocchi|\bravioli|\btortellini|\bfarfalle|\borecchiette|\bziti\b|\bbucatini\b|\btagliatelle\b|\bpappardelle\b|\bcavatappi\b|\belbows?\b|\bangel hair\b|\bshells\b|\bditalini\b|\bcapellini\b|\bfusilli\b|\brotini\b|\bconchiglie\b/, "🍝"],
  [/\bsushi rice\b|\bonigiri/, "🍙"],
  [/\brice\b|\bbasmati|\bjasmine|\barborio|\bcarnaroli|\brisotto|\bwild rice/, "🍚"],
  [/\bquinoa|\bcouscous|\bbulgur|\bfarro|\bbarley|\bmillet|\bfreekeh|\bspelt|\bsorghum|\bwheat berries|\bsemolina|\bwheat\b|\boats?\b|\boatmeal|\bgranola|\bmuesli|\brolled oats|\bflour\b|\bcornflour\b|\btapioca (flour|starch)\b|\barrowroot\b|\bpotato starch\b|\bstarch\b|\bnutritional yeast\b|\bwheat germ\b|\bbran\b/, "🌾"],
  [/\btapioca pearls?\b|\bboba\b/, "🧋"],
  [/\bcrackers?\b|\bchips\b|\btortilla chips\b|\bpita chips\b|\bpopcorn\b|\bsaltines?\b/, "🍘"],

  // ---- nuts and seeds ----
  [/\bpeanut butter\b|\bpeanuts?\b|\balmond butter\b|\bcashew butter\b|\bnut butter\b/, "🥜"],
  [/\bchestnuts?\b|\balmonds?\b|\bwalnuts?\b|\bpecans?\b|\bcashews?\b|\bpistachios?\b|\bhazelnuts?\b|\bmacadamias?\b|\bpine nuts?\b|\bnuts?\b|\bmarzipan\b|\balmond (extract|flour|meal|paste)\b/, "🌰"],
  [/\btahini\b|\bsesame\b|\bsunflower\b|\bpumpkin seeds?\b|\bpepitas?\b|\bchia\b|\bflax|\bhemp\b|\bpoppy seeds?\b|\bseeds?\b/, "🌻"],

  // ---- sweet things and baking ----
  [/\bchocolate|\bcocoa\b|\bcacao\b|\bnutella\b/, "🍫"],
  [/\bvanilla\b/, "🌼"],
  [/\bhoney\b|\bmolasses\b|\btreacle\b|\bgolden syrup\b/, "🍯"],
  [/\bmaple\b/, "🍁"],
  [/\bagave\b/, "🌵"],
  [/\bjam\b|\bjelly\b|\bmarmalade\b|\bpreserves\b|\bchutney\b|\bfruit spread\b|\bcompote\b/, "🫙"],
  [/\bcaramel|\bdulce de leche|\btoffee|\bbutterscotch|\bcustard\b|\bpudding\b|\bgelatin\b|\bjello\b|\bagar\b|\bflan\b/, "🍮"],
  [/\bsprinkles\b|\bnonpareils\b|\bicing\b|\bfrosting\b|\bglaze\b|\bfondant\b|\bbaking (powder|soda)\b|\bcream of tartar\b|\bcupcake liners?\b/, "🧁"],
  [/\byeast\b/, "🍞"],
  [/\bsugar\b|\bcandy\b|\bcandies\b|\bmarshmallows?\b|\bjaggery\b|\bsweetener\b|\bstevia\b|\bsplenda\b|\bhoneycomb\b|\bcandied\b/, "🍬"],
  [/\bcookies?\b|\bbiscotti\b|\bgraham\b|\bwafers?\b|\bladyfingers?\b|\boreos?\b|\bgingersnaps?\b/, "🍪"],
  [/\bfood colou?ring\b|\bgel colou?r\b/, "🎨"],
  [/\bpie\b/, "🥧"],
  [/\bcake\b|\bbrownies?\b/, "🍰"],

  // ---- bottles, oils, condiments ----
  [/\bvinegar\b|\bsoy sauce\b|\bshoyu\b|\btamari\b|\bliquid aminos\b|\bcoconut aminos\b|\bfish sauce\b|\bworcest(er)?shire\b|\bworcester\b|\bworchest|\boyster sauce\b|\bhoisin\b|\bmirin\b|\bsake\b|\brice wine\b|\bshaoxing\b|\bextract\b|\bessence\b|\bponzu\b|\bteriyaki\b|\bkecap\b|\bcooking wine\b/, "🍶"],
  [/\bketchup\b|\bcatsup\b|\bbbq sauce\b|\bbarbecue sauce\b|\bsteak sauce\b|\bcocktail sauce\b/, "🥫"],
  [/\bmustard\b|\bmayonnaise\b|\bmayo\b|\baioli\b|\bhorseradish\b|\bwasabi\b|\brelish\b|\branch\b|\bdressing\b|\bvinaigrette\b|\bsauce\b|\bpaste\b|\bspread\b|\btapenade\b|\bpickled\b/, "🫙"],
  [/\bcooking spray\b|\bnonstick spray\b|\bpan spray\b/, "🫧"],
  [/\bsesame oil\b|\bvegetable oil\b|\bcanola\b|\bsunflower oil\b|\bgrapeseed\b|\bpeanut oil\b|\bavocado oil\b|\bneutral oil\b|\bcorn oil\b|\bcoconut oil\b|\bcooking oil\b|\boil\b/, "🌻"],

  // ---- drinks ----
  [/\bcoffee\b|\bespresso\b/, "☕"],
  [/\btea\b|\bmatcha\b|\bchai\b/, "🍵"],
  [/\bwine\b|\bsherry\b|\bvermouth\b|\bmarsala\b|\bport\b|\bmadeira\b/, "🍷"],
  [/\bchampagne\b|\bprosecco\b|\bcava\b/, "🍾"],
  [/\bbeer\b|\bale\b|\blager\b|\bstout\b|\bcider\b|\bpilsner\b/, "🍺"],
  [/\bwhisk(e)?y\b|\bbourbon\b|\brum\b|\bbrandy\b|\bcognac\b|\bvodka\b|\btequila\b|\bmezcal\b|\bgin\b|\bliqueur\b|\bkahl[uú]a\b|\bamaretto\b|\bcointreau\b|\btriple sec\b|\bgrand marnier\b|\bkirsch\b|\bbitters\b/, "🥃"],
  [/\bjuice\b|\bnectar\b/, "🧃"],
  [/\bsoda\b|\bcola\b|\bginger ale\b|\bsparkling water\b|\bseltzer\b|\bclub soda\b|\btonic\b|\blemonade\b/, "🥤"],
  [/\bice\b|\bice cubes\b|\bcrushed ice\b/, "🧊"],
  [/\bwater\b/, "💧"],

  // ---- salt and pepper, last, so "celery salt" and "lemon pepper" were caught above ----
  [/\bsalt\b|\bpeppercorns?\b|\bpepper\b/, "🧂"],

  // ---- kitchen, not food ----
  [/\bwood pellets\b|\bwood chips\b|\bcharcoal\b|\bskewers?\b|\bparchment\b|\bfoil\b|\btwine\b/, "🪵"],
];

const cache = new Map<string, string | null>();

/** The flora/fauna (or pantry) emoji for an ingredient's text, or null when nothing fits. */
export function foodEmoji(text: string): string | null {
  const key = text.toLowerCase().replace(/[’‘]/g, "'");
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  let found: string | null = null;
  for (const [pattern, emoji] of RULES) {
    if (pattern.test(key)) {
      found = emoji || null;
      break;
    }
  }
  cache.set(key, found);
  return found;
}
