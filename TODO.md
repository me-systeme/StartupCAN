value id used for validation

value_old könnte none sein

-->

testen: current mit und ohne value id. 
mit value id: falsch und richtig

testen current und new mit value id = ans id
value != ans id

testen new ohne value id

default_value_id = default_cmd_id testen

_assert_unique_can_fields testen

_assert_can_id_range testen


gibt es noch logikfehler? gibt es konzept probleme? würdest du das in production so lassen? wenn nein, warum? was ist schlecht an der lösung? ist das eine saubere lösung? bitte refactore auf best practice


Mittelfristig würde ich trennen in:

config_loader.py
config_validation.py
config_runtime.py

