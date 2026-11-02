void *Cache_AllocResourceBlock(int size, void *owner, int mode);
void *read_cache(void) { return Cache_AllocResourceBlock(1, 0, 0); }
