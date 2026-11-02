struct Player;
void Achieve_ApplyPlayerOptionOverrides(struct Player *player);
void call_override(struct Player *player) { Achieve_ApplyPlayerOptionOverrides(player); }
