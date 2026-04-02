## prioritize: SIMPLE, PREDICTABLE (DETERMINISTIC), EASY TO REASON ABOUT AND DEBUG


› is there a way of chaining stuff without all that indentations?
fn field_value(field: String, key: String) -> Maybe String =
match string.split_once(field, "=") with
| Nothing -> Nothing
| Just pair ->
match pair with
| (name, value) ->
if name == key then Just(value)
else Nothing

fn attr_value(raw: String, key: String) -> Maybe String =
match string.split_once(raw, ",") with
| Nothing -> field_value(raw, key)
| Just pair ->
match pair with
| (field, rest) ->
match field_value(field, key) with
| Just value -> Just(value)
| Nothing -> attr_value(rest, key)


• Partially, today, and a bit more now.

What you want here is one of:

- local let/where inside expression branches
- bind-pattern destructuring inside `do`
- monadic/applicative combinators for Maybe
- a small helper combinator layer like maybe_then

Right now Sprout only gives you:

- match
- function calls
- function-local where

So this kind of code naturally turns into nested match.

The closest improvements available today are:

1. Use nested patterns directly in `match`

fn field_value(field: String, key: String) -> Maybe String =
match string.split_once(field, "=") with
| Just (name, value) -> if name == key then Just(value) else Nothing
| Nothing -> Nothing

2. Use helper functions to flatten one step

fn field_value(field: String, key: String) -> Maybe String =
match string.split_once(field, "=") with
| Nothing -> Nothing
| Just pair -> field_value_from_pair(pair, key)

fn field_value_from_pair(pair: (String, String), key: String) -> Maybe String =
match pair with
| (name, value) ->
if name == key then Just(value)
else Nothing

3. Use where only for derived pure values, not for match chaining
   Helpful sometimes, but not enough here.
4. Add a small stdlib helper for Maybe
   For example:

fn and_then(m: Maybe a, f: a -> Maybe b) -> Maybe b =
match m with
| Nothing -> Nothing
| Just x -> f(x)

Then your code could become much flatter:

fn field_value(field: String, key: String) -> Maybe String =
and_then(string.split_once(field, "="), \pair ->
match pair with
| (name, value) ->
if name == key then Just(value) else Nothing
)

That still leaves one real gap: carrying destructuring through sequencing without
falling back to a second nested `match`.

So the real answer is:

- today: `match` already supports nested constructor/tuple patterns like `Just (a, b)`
- language-wise: the next syntax win is irrefutable bind-pattern support in `do`, especially tuple binds like `(a, b) <- expr`
- library-wise: a small Maybe.and_then / Maybe.map layer would help a lot too

For your exact example, the `match` part can already become:

fn field_value(field: String, key: String) -> Maybe String =
match string.split_once(field, "=") with
| Just (name, value) -> if name == key then Just(value) else Nothing
| Nothing -> Nothing

fn attr_value(raw: String, key: String) -> Maybe String =
match string.split_once(raw, ",") with
| Just (field, rest) ->
match field_value(field, key) with
| Just value -> Just(value)
| Nothing -> attr_value(rest, key)
| Nothing -> field_value(raw, key)

The next most valuable syntax improvement after function-local where is probably
keeping that same destructuring style available in `do`, while staying
predictable by restricting bind patterns to irrefutable ones.
