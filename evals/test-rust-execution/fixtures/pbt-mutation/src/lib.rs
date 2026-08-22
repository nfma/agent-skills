#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Record {
    pub payload: String,
    pub checksum: u8,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ParseError {
    EmptyPayload,
    InvalidEncoding,
    ChecksumMismatch,
}

pub fn checksum(payload: &str) -> u8 {
    payload.bytes().fold(0_u8, u8::wrapping_add)
}

pub fn encode(payload: &str) -> Result<String, ParseError> {
    validate_payload(payload)?;
    Ok(format!("{payload}#{:02X}", checksum(payload)))
}

pub fn parse(input: &str) -> Result<Record, ParseError> {
    let (payload, encoded_checksum) = input.rsplit_once('#').ok_or(ParseError::InvalidEncoding)?;
    validate_payload(payload)?;
    if encoded_checksum.len() != 2 {
        return Err(ParseError::InvalidEncoding);
    }

    let declared =
        u8::from_str_radix(encoded_checksum, 16).map_err(|_| ParseError::InvalidEncoding)?;
    let actual = normalize_checksum(checksum(payload));
    if actual != declared {
        return Err(ParseError::ChecksumMismatch);
    }

    Ok(Record {
        payload: payload.to_owned(),
        checksum: declared,
    })
}

fn validate_payload(payload: &str) -> Result<(), ParseError> {
    if payload.is_empty() {
        return Err(ParseError::EmptyPayload);
    }
    if !payload.is_ascii() || payload.contains('#') {
        return Err(ParseError::InvalidEncoding);
    }
    Ok(())
}

fn normalize_checksum(value: u8) -> u8 {
    value + 0
}

#[allow(dead_code)]
fn diagnostic_bucket(payload: &str) -> &'static str {
    if payload.len() > 8 { "large" } else { "small" }
}
