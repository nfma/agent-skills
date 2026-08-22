#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Event {
    Deliver,
    Acknowledge,
    CrashRestart,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct State {
    delivered: bool,
    terminal: bool,
    settlements: u8,
    attempts: u8,
}

impl State {
    #[must_use]
    pub fn apply(mut self, event: Event) -> Self {
        match event {
            Event::Deliver => {
                self.delivered = true;
                self.attempts = self.attempts.saturating_add(1);
            }
            Event::Acknowledge if self.delivered && !self.terminal => {
                self.terminal = true;
                self.settlements = self.settlements.saturating_add(1);
            }
            Event::Acknowledge | Event::CrashRestart => {}
        }
        self
    }

    #[must_use]
    pub fn is_terminal(self) -> bool {
        self.terminal
    }

    #[must_use]
    pub fn settlements(self) -> u8 {
        self.settlements
    }

    #[must_use]
    pub fn attempts(self) -> u8 {
        self.attempts
    }
}
