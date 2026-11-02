# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_SPARKYFITNESS_POSTGRES_PASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-sparkyfitness-postgres_password")" | cut -c1-16)"
export APP_THAINAN_SPARKYFITNESS_SPARKY_FITNESS_API_ENCRYPTION_KEY="$(printf '%s' "$(derive_entropy "env-thainan-sparkyfitness-sparky_fitness_api_encryption_key")" | cut -c1-64)"
export APP_THAINAN_SPARKYFITNESS_BETTER_AUTH_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-sparkyfitness-better_auth_secret")" | cut -c1-64)"
