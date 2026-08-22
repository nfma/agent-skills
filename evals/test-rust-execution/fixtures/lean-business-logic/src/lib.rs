pub fn apply_credits(subtotal: u64, credits: &[u64]) -> u64 {
    credits.iter().fold(subtotal, |remaining, credit| {
        remaining.saturating_sub(*credit)
    })
}
